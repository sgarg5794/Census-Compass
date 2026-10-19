"""Fail-closed SQL policy validation (spec §8, §9.1-§9.3).

Every query the LLM writes passes through :func:`validate_and_fix` before it reaches
Snowflake. Parsing alone is never authorization: anything that is not positively recognized
as one of the supported shapes is rejected with :class:`QueryRejected`.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterator
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.scope import Scope, build_scope

from census_compass.contracts import BuilderMetadata, QueryRejected

_KEY = "CENSUS_BLOCK_GROUP"
_FIPS_SUFFIX = "_METADATA_CBG_FIPS_CODES"
_YEARS = ("2019", "2020")
_MAX_PASSES = 4
_QUALIFY_HINT = (
    'Select explicit, table-qualified columns ("TABLE"."COLUMN") instead of SELECT * '
    "or ambiguous names."
)
_JOIN_HINT = (
    'Allowed joins: "<A>"."CENSUS_BLOCK_GROUP" = "<B>"."CENSUS_BLOCK_GROUP" between two '
    'same-year tables, or LEFT("<A>"."CENSUS_BLOCK_GROUP", 5) = '
    '"<YEAR>_METADATA_CBG_FIPS_CODES"."STATE_FIPS" || '
    '"<YEAR>_METADATA_CBG_FIPS_CODES"."COUNTY_FIPS".'
)
_TABLE_ARGS = {"this", "db", "catalog", "alias"}
# Only these otherwise-unknown (Anonymous) functions are accepted, and only inside OVER().
_ANONYMOUS_WINDOW_FUNCTIONS = {"RATIO_TO_REPORT"}

_FORBIDDEN_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "Command", "TruncateTable",
    "Copy", "Use", "Set", "Transaction", "Commit", "Rollback", "Grant", "Into", "Lateral",
    "UDTF", "Explode", "TableFromRows", "TableSample", "Pivot", "Values", "SetOperation",
    "Parameter", "Placeholder", "SessionParameter", "DynamicIdentifier", "Connect",
    "MatchRecognize", "HistoricalData", "Changes", "Unnest",
)  # fmt: skip
_FORBIDDEN = tuple(
    t for t in (getattr(exp, n, None) for n in _FORBIDDEN_NAMES) if isinstance(t, type)
)

Leaves = frozenset[tuple[str, str]]


@dataclass
class _Ctx:
    metadata: BuilderMetadata
    allowed: list[str]
    max_rows: int
    database: str
    schema: str
    dimensions: set[tuple[str, str]]
    all_columns: set[str]
    memo: dict[tuple[int, str], Leaves] = field(default_factory=dict)


def validate_and_fix(
    sql: str,
    metadata: BuilderMetadata,
    allowed: list[str],
    max_rows: int,
    database: str,
    schema: str,
) -> str:
    """Return the fixed, fully qualified SQL, or raise ``QueryRejected``.

    Fixes (quoting, ``database.schema`` prefix, alias removal, column qualification, row
    limit) produce new SQL that is validated again from the start until it is stable.
    """
    if not isinstance(max_rows, int) or isinstance(max_rows, bool) or max_rows < 1:
        raise ValueError("max_rows must be a positive integer")
    dimensions = {
        (m.table, m.source_column)
        for m in metadata.members
        if m.kind == "dimension" and m.source_column and m.source_column != _KEY
    }
    all_columns = {c for cols in metadata.table_columns.values() for c in cols}
    current = sql
    for _ in range(_MAX_PASSES):
        ctx = _Ctx(metadata, list(allowed), max_rows, database, schema, dimensions, all_columns)
        fixed = _validate_once(current, ctx)
        if fixed == current:
            return fixed
        current = fixed
    raise QueryRejected("The query could not be normalized into a stable, validated form.")


# --------------------------------------------------------------------------- one pass


def _validate_once(sql: str, ctx: _Ctx) -> str:
    stmt = _parse_single_select(sql)
    _reject_forbidden(stmt)
    _normalize_identifiers(stmt, ctx)
    _check_and_rewrite_tables(stmt, ctx)
    try:
        root = build_scope(stmt)
        if root is None:
            raise QueryRejected("Only a single SELECT statement is supported.")
        for scope in root.traverse():
            _check_scope(scope, ctx)
    except OptimizeError as e:
        raise QueryRejected(f"The query structure could not be analysed: {e}") from e
    _apply_limit(stmt, ctx.max_rows)
    return stmt.sql(dialect="snowflake", identify=True)


def _parse_single_select(sql: str) -> exp.Select:
    try:
        statements = [s for s in sqlglot.parse(sql, read="snowflake") if s is not None]
    except (ParseError, TokenError) as e:
        raise QueryRejected(f"The query does not parse as Snowflake SQL: {e}") from e
    if len(statements) != 1:
        raise QueryRejected(f"Exactly one SELECT statement is allowed; found {len(statements)}.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Select):
        raise QueryRejected(
            f"Only a single SELECT (optionally WITH ... SELECT) is allowed; got {stmt.key.upper()}."
        )
    return stmt


def _reject_forbidden(stmt: exp.Select) -> None:
    for node in stmt.walk():
        if isinstance(node, _FORBIDDEN):
            raise QueryRejected(f"Unsupported SQL construct: {node.key.upper()}.")
        if isinstance(node, exp.Anonymous):
            name = node.name.upper()
            if not (name in _ANONYMOUS_WINDOW_FUNCTIONS and isinstance(node.parent, exp.Window)):
                raise QueryRejected(f"Unsupported function: {node.name}.")


def _normalize_identifiers(stmt: exp.Select, ctx: _Ctx) -> None:
    """Apply Snowflake semantics to unquoted identifiers (they resolve upper case).

    Exception: an unquoted name that exactly matches a physical column or allowed table is
    kept as written, so it gets quoted with its real mixed case.
    """
    for ident in list(stmt.find_all(exp.Identifier)):
        if ident.quoted:
            continue
        parent, key = ident.parent, ident.arg_key
        if isinstance(parent, exp.Column) and key == "this" and ident.name in ctx.all_columns:
            continue
        if isinstance(parent, exp.Table) and key == "this" and ident.name in ctx.allowed:
            continue
        ident.set("this", ident.name.upper())


# --------------------------------------------------------------------------- tables


def _year(table: str) -> str:
    year = table[:4]
    if year not in _YEARS or table[4:5] != "_":
        raise QueryRejected(f"Cannot determine the year of table {table!r}.")
    return year


def _is_fips(table: str) -> bool:
    return table.endswith(_FIPS_SUFFIX)


def _is_physical(table: exp.Expr) -> bool:
    """Physical tables carry the catalog prefix added by the rewrite; CTE references do not."""
    return isinstance(table, exp.Table) and bool(table.args.get("catalog"))


def _check_prefix(node: exp.Expr, ctx: _Ctx, what: str) -> None:
    catalog, db = node.args.get("catalog"), node.args.get("db")
    if catalog is not None and catalog.name != ctx.database:
        raise QueryRejected(f"{what} must be in database {ctx.database}; got {catalog.name}.")
    if db is not None and db.name != ctx.schema:
        raise QueryRejected(f"{what} must be in schema {ctx.schema}; got {db.name}.")


def _cte_names(stmt: exp.Select, ctx: _Ctx) -> list[str]:
    names: list[str] = []
    for cte in stmt.find_all(exp.CTE):
        alias = cte.args.get("alias")
        if alias is not None and alias.args.get("columns"):
            raise QueryRejected(f"CTE column lists are not supported. {_QUALIFY_HINT}")
        names.append(cte.alias)
    if len(names) != len(set(names)):
        raise QueryRejected("Each CTE name may be defined only once.")
    for name in names:
        if name in ctx.allowed or name in ctx.metadata.table_columns:
            raise QueryRejected(f"CTE name {name!r} shadows a table name; choose another name.")
    return names


def _derived_aliases(stmt: exp.Select) -> list[str]:
    names: list[str] = []
    for sub in stmt.find_all(exp.Subquery):
        alias = sub.args.get("alias")
        if alias is not None:
            if alias.args.get("columns"):
                raise QueryRejected(
                    f"Derived-table column lists are not supported. {_QUALIFY_HINT}"
                )
            names.append(alias.name)
    return names


def _check_and_rewrite_tables(stmt: exp.Select, ctx: _Ctx) -> None:
    cte_names = _cte_names(stmt, ctx)
    derived_aliases = _derived_aliases(stmt)
    physical: list[str] = []
    aliases: dict[str, str] = {}
    alias_names: list[str] = []
    for table in list(stmt.find_all(exp.Table)):
        extra = [k for k, v in table.args.items() if v and k not in _TABLE_ARGS]
        if extra:
            raise QueryRejected(f"Unsupported table clause: {', '.join(extra).upper()}.")
        if not isinstance(table.this, exp.Identifier):
            raise QueryRejected("Only plain table names are allowed (no stages or functions).")
        name = table.name
        if not table.args.get("db") and not table.args.get("catalog") and name in cte_names:
            if table.alias:
                alias_names.append(table.alias)
            continue
        _check_prefix(table, ctx, f"Table {name}")
        if name not in ctx.allowed:
            raise QueryRejected(
                f"Table {name!r} is not allowed.",
                difflib.get_close_matches(name, ctx.allowed, n=3),
            )
        physical.append(name)
        if table.alias:
            aliases[table.alias] = name
            alias_names.append(table.alias)
            table.set("alias", None)
        table.set("catalog", exp.to_identifier(ctx.database, quoted=True))
        table.set("db", exp.to_identifier(ctx.schema, quoted=True))

    repeated = sorted({t for t in physical if physical.count(t) > 1})
    if repeated:
        raise QueryRejected(
            f"Table {repeated[0]} appears more than once in the query; each table may be used "
            "only once (no self-joins or repeated scans)."
        )
    names = alias_names + derived_aliases + cte_names
    for alias, table_name in aliases.items():
        if names.count(alias) > 1 or (alias in physical and alias != table_name):
            raise QueryRejected(
                f"Alias {alias!r} is used for more than one source; use distinct aliases."
            )
    _check_year_and_source(physical, ctx)

    for col in stmt.find_all(exp.Column):
        if col.args.get("db") or col.args.get("catalog"):
            _check_prefix(col, ctx, f"Column {col.name}")
            col.set("db", None)
            col.set("catalog", None)
        if col.table in aliases:
            col.set("table", exp.to_identifier(aliases[col.table], quoted=True))


def _check_year_and_source(physical: list[str], ctx: _Ctx) -> None:
    years = sorted({_year(t) for t in physical})
    if len(years) > 1:
        raise QueryRejected(
            "2019 and 2020 tables cannot be combined in one query; query each year separately."
        )
    families = {
        "census count" if t.startswith(f"{years[0]}_REDISTRICTING") else "ACS"
        for t in physical
        if not _is_fips(t)
    }
    if len(families) > 1:
        raise QueryRejected(
            "Census count (redistricting) and ACS tables cannot be combined in one query."
        )
    for t in physical:
        if t not in ctx.metadata.table_columns:
            raise QueryRejected(f"No column metadata for table {t!r}; it cannot be validated.")


# --------------------------------------------------------------------------- scopes


def _is_query(node: exp.Expr) -> bool:
    return isinstance(node, (exp.Query, exp.Subquery, exp.With, exp.CTE))


def _local_nodes(expr: exp.Expr) -> Iterator[exp.Expr]:
    """Nodes of ``expr`` that belong to its own scope (child queries are not entered)."""
    for node in expr.walk(prune=lambda n: n is not expr and _is_query(n)):
        if node is expr or not _is_query(node):
            yield node


def _check_scope(scope: Scope, ctx: _Ctx) -> None:
    select = scope.expression
    if not isinstance(select, exp.Select):
        raise QueryRejected("Only plain SELECT query blocks are supported.")
    sources = _selected(scope)
    for node in list(_local_nodes(select)):
        if not isinstance(node, exp.Column):
            continue
        if isinstance(node.this, exp.Star):
            if node.table and node.table not in sources:
                raise QueryRejected(f"Unknown table or alias {node.table!r}.")
            continue
        _leaves(node, scope, ctx, frozenset())
    _check_joins(select)
    for node in list(_local_nodes(select)):
        if isinstance(node, exp.AggFunc):
            target = node.parent if isinstance(node.parent, exp.WithinGroup) else node
            label = f"Aggregate {node.key.upper()}"
        elif isinstance(node, exp.Window):
            target, label = node.this, "Window function"
        else:
            continue
        dims = sorted(_expr_leaves(target, scope, ctx, frozenset()) & ctx.dimensions)
        if dims:
            table, column = dims[0]
            raise QueryRejected(
                f'{label} over dimension "{table}"."{column}" is not allowed: published '
                "medians, averages and ratios cannot be aggregated across block groups. Use "
                "the corresponding measure instead."
            )


def _selected(scope: Scope) -> dict[str, exp.Table | Scope]:
    return {name: src for name, (_, src) in scope.selected_sources.items()}


def _outputs(source: exp.Table | Scope, ctx: _Ctx) -> list[str] | None:
    """Known output columns of a source, or ``None`` when a star hides them."""
    if isinstance(source, exp.Table):
        return list(ctx.metadata.table_columns.get(source.name, []))
    select = source.expression
    if not isinstance(select, exp.Select):
        return None
    for e in select.expressions:
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
            return None
    return [e.alias_or_name for e in select.expressions]


def _source_leaves(
    source: exp.Table | Scope, name: str, ctx: _Ctx, visiting: frozenset[int]
) -> Leaves:
    if isinstance(source, exp.Table):
        if not _is_physical(source):
            raise QueryRejected(f"Unresolved table reference {source.name!r}.")
        columns = ctx.metadata.table_columns.get(source.name, [])
        if name not in columns:
            raise QueryRejected(
                f'Column "{name}" does not exist in table {source.name}.',
                difflib.get_close_matches(name, columns, n=3),
            )
        return frozenset({(source.name, name)})
    outputs = _outputs(source, ctx)
    if outputs is None:
        raise QueryRejected(f'Column "{name}" cannot be traced through SELECT *. {_QUALIFY_HINT}')
    matches = [e for e in source.expression.expressions if e.alias_or_name == name]
    if not matches:
        raise QueryRejected(
            f'Column "{name}" is not produced by the subquery or CTE.',
            difflib.get_close_matches(name, outputs, n=3),
        )
    if len(matches) > 1:
        raise QueryRejected(f'Column "{name}" is produced more than once; it is ambiguous.')
    key = (id(source), name)
    if key not in ctx.memo:
        ctx.memo[key] = _expr_leaves(matches[0], source, ctx, visiting)
    return ctx.memo[key]


def _expr_leaves(expr: exp.Expr, scope: Scope, ctx: _Ctx, visiting: frozenset[int]) -> Leaves:
    """Physical leaves of every column in ``expr``; nested subqueries are untraceable."""
    if any(n is not expr and _is_query(n) for n in expr.walk()):
        raise QueryRejected(
            f"A value computed by a nested subquery cannot be traced here. {_QUALIFY_HINT}"
        )
    leaves: set[tuple[str, str]] = set()
    for node in expr.walk():
        if isinstance(node, exp.Column) and not isinstance(node.this, exp.Star):
            leaves |= _leaves(node, scope, ctx, visiting)
    return frozenset(leaves)


def _projection_alias(scope: Scope, name: str, col: exp.Column) -> exp.Alias | None:
    for e in scope.expression.expressions:
        if isinstance(e, exp.Alias) and e.alias == name:
            if any(n is col for n in e.walk()):
                return None  # a projection cannot reference its own alias
            return e
    return None


def _leaves(col: exp.Column, scope: Scope, ctx: _Ctx, visiting: frozenset[int]) -> Leaves:
    """Physical (table, column) leaves a column reference traces to; rejects when uncertain."""
    if id(col) in visiting:
        raise QueryRejected(f'Column "{col.name}" refers to itself. {_QUALIFY_HINT}')
    visiting = visiting | {id(col)}
    name, qualifier = col.name, col.table
    if qualifier:
        current: Scope | None = scope
        while current is not None:
            sources = _selected(current)
            if qualifier in sources:
                return _source_leaves(sources[qualifier], name, ctx, visiting)
            current = current.parent if current.is_subquery else None
        raise QueryRejected(f"Unknown table or alias {qualifier!r} in column {qualifier}.{name}.")

    sources = _selected(scope)
    hits: list[str] = []
    known: list[str] = []
    for source_name, source in sources.items():
        outputs = _outputs(source, ctx)
        if outputs is None:
            raise QueryRejected(f'Column "{name}" cannot be traced with certainty. {_QUALIFY_HINT}')
        known.extend(outputs)
        if name in outputs:
            hits.append(source_name)
    if len(hits) > 1:
        raise QueryRejected(f'Column "{name}" is ambiguous ({", ".join(hits)}). {_QUALIFY_HINT}')
    alias = _projection_alias(scope, name, col)
    if hits:
        passthrough = (
            isinstance(alias, exp.Alias)
            and isinstance(alias.this, exp.Column)
            and (alias.this.name == name)
        )
        if alias is not None and not passthrough:
            raise QueryRejected(f'"{name}" is both a column and a select alias; it is ambiguous.')
        col.set("table", exp.to_identifier(hits[0], quoted=True))
        return _source_leaves(sources[hits[0]], name, ctx, visiting)
    if alias is not None:
        return _expr_leaves(alias.this, scope, ctx, visiting)
    raise QueryRejected(f'Unknown column "{name}".', difflib.get_close_matches(name, known, n=3))


# --------------------------------------------------------------------------- joins


def _strip(node: exp.Expr) -> exp.Expr:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _key_column(node: exp.Expr, column: str) -> str | None:
    node = _strip(node)
    if isinstance(node, exp.Column) and node.name == column and node.table:
        return node.table
    return None


def _fips_side(node: exp.Expr) -> str | None:
    node = _strip(node)
    if not isinstance(node, exp.DPipe):
        return None
    state = _key_column(node.this, "STATE_FIPS")
    county = _key_column(node.expression, "COUNTY_FIPS")
    return state if state and state == county else None


def _county_side(node: exp.Expr) -> str | None:
    node = _strip(node)
    if not isinstance(node, exp.Left):
        return None
    length = _strip(node.expression)
    if not (isinstance(length, exp.Literal) and not length.is_string and length.name == "5"):
        return None
    return _key_column(node.this, _KEY)


def _join_pair(on: exp.Expr) -> tuple[str, str] | None:
    on = _strip(on)
    if not isinstance(on, exp.EQ):
        return None
    left, right = on.this, on.expression
    a, b = _key_column(left, _KEY), _key_column(right, _KEY)
    if a and b and a != b and not _is_fips(a) and not _is_fips(b):
        return a, b
    for demo_side, fips_side in ((left, right), (right, left)):
        demo, fips = _county_side(demo_side), _fips_side(fips_side)
        if demo and fips and not _is_fips(demo) and fips == f"{demo[:4]}{_FIPS_SUFFIX}":
            return demo, fips
    return None


def _check_joins(select: exp.Select) -> None:
    joins = select.args.get("joins") or []
    if not joins:
        return
    from_ = select.args.get("from_") or select.args.get("from")
    base = from_.this if from_ is not None else None
    if not _is_physical(base):
        raise QueryRejected(f"Joins are only allowed between tables. {_JOIN_HINT}")
    seen = {base.name}
    for join in joins:
        target = join.this
        if not _is_physical(target):
            raise QueryRejected(f"Joins are only allowed between tables. {_JOIN_HINT}")
        side = (join.side or "").upper()
        kind = (join.kind or "").upper()
        if (
            join.args.get("method")
            or join.args.get("using")
            or join.args.get("on") is None
            or (side, kind) not in {("", ""), ("", "INNER"), ("LEFT", ""), ("LEFT", "OUTER")}
        ):
            raise QueryRejected(
                f"Only INNER or LEFT joins with an ON clause are allowed. {_JOIN_HINT}"
            )
        pair = _join_pair(join.args["on"])
        if pair is None or target.name not in pair:
            raise QueryRejected(f"Join condition is not one of the two allowed forms. {_JOIN_HINT}")
        other = pair[1] if pair[0] == target.name else pair[0]
        if other not in seen:
            raise QueryRejected(
                f"Join condition must connect {target.name} to an earlier table. {_JOIN_HINT}"
            )
        seen.add(target.name)


# --------------------------------------------------------------------------- limit


def _apply_limit(stmt: exp.Select, max_rows: int) -> None:
    limit = stmt.args.get("limit")
    if limit is None:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
        return
    if isinstance(limit, exp.Fetch):
        options = limit.args.get("limit_options")
        if options is not None and (options.args.get("percent") or options.args.get("with_ties")):
            raise QueryRejected("FETCH ... PERCENT / WITH TIES is not supported; use LIMIT.")
        value = limit.args.get("count")
    else:
        value = limit.expression
    value = _strip(value) if value is not None else None
    if not (isinstance(value, exp.Literal) and not value.is_string and value.name.isdigit()):
        raise QueryRejected("LIMIT must be a whole-number literal.")
    stmt.set("limit", exp.Limit(expression=exp.Literal.number(min(int(value.name), max_rows))))
