"""Completeness augmentation (spec §6.2, §6.3, §9.5).

After :func:`validate_and_fix`, every aggregate output of the final ``SELECT`` gets companion
expressions computed in the same ``SELECT`` that produces the aggregate (so they share its
``FROM``, joins, ``WHERE`` and ``GROUP BY``): how many block groups it covers, how many had
each input missing and, for ratios, coverage. Companions computed in a CTE or derived table are
carried through outer levels that only pass rows through; anything else is rejected.
:func:`split_result` removes the companion columns from the rows again.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError, TokenError
from sqlglot.optimizer.scope import Scope, build_scope

from census_compass.contracts import BuilderMetadata, Completeness, QueryRejected

CompanionKind = Literal[
    "block_groups", "missing_input", "coverage", "unknown_denominator", "missing_moe"
]

_PREFIX = "__cc_"
_SINGLE_LEVEL = (
    "Completeness cannot be carried through this query. Rewrite it as a single-level "
    "aggregation: compute each total or rate in one SELECT (optionally inside a CTE that outer "
    "levels only select from, rank, filter, order or limit), without re-aggregating, combining "
    "or windowing aggregated values."
)
_RANKING = (exp.RowNumber, exp.Rank, exp.DenseRank, exp.PercentRank, exp.CumeDist, exp.Ntile)
_FILTER_CLAUSES = (exp.Where, exp.Having, exp.Qualify)


@dataclass(frozen=True)
class Companion:
    alias: str
    output_column: str
    kind: CompanionKind
    input_column: str | None = None


@dataclass(frozen=True)
class AugmentedQuery:
    sql: str
    companions: list[Companion] = field(default_factory=list)


def normalize_measure_sql(sql: str) -> str:
    """Canonical Snowflake text of a measure expression, used for matching (ruling 18)."""
    return sqlglot.parse_one(sql, read="snowflake").sql(dialect="snowflake")


# --------------------------------------------------------------------------- augment


@dataclass(frozen=True)
class _Spec:
    """A companion as seen at one query level: ``alias`` is its column name there."""

    kind: CompanionKind
    input_column: str | None
    alias: str


@dataclass
class _Level:
    aggregated: bool  # rows of this level are aggregated rows, not block groups
    carries: dict[str, list[_Spec]]  # output column -> its companions
    outputs: list[str]  # output columns before companions were added


@dataclass
class _Measure:
    expression: exp.Expr
    inputs: list[tuple[str, str]]
    denominators: list[tuple[str, str]]
    coverage: exp.Expr


def augment(sql: str, metadata: BuilderMetadata) -> AugmentedQuery:
    """Add completeness companions to validated SQL, or raise ``QueryRejected``."""
    try:
        tree = sqlglot.parse_one(sql, read="snowflake")
    except (ParseError, TokenError) as e:
        raise QueryRejected(f"The query does not parse as Snowflake SQL: {e}") from e
    if not isinstance(tree, exp.Select):
        raise QueryRejected("Only a single SELECT statement is supported.")
    for ident in tree.find_all(exp.Identifier):
        if ident.name.lower().startswith(_PREFIX):
            raise QueryRejected(f"Names starting with {_PREFIX} are reserved; rename {ident.name}.")
    try:
        root = build_scope(tree)
    except OptimizeError as e:
        raise QueryRejected(f"The query structure could not be analysed: {e}") from e
    if root is None:
        raise QueryRejected("Only a single SELECT statement is supported.")

    augmenter = _Augmenter(_measures(metadata))
    augmenter.check_unreachable(root)
    level = augmenter.level(root)
    if not augmenter.changed:
        return AugmentedQuery(sql=sql, companions=[])
    companions = [
        Companion(spec.alias, column, spec.kind, spec.input_column)
        for column, specs in level.carries.items()
        for spec in specs
    ]
    return AugmentedQuery(sql=tree.sql(dialect="snowflake", identify=True), companions=companions)


def _measures(metadata: BuilderMetadata) -> list[_Measure]:
    measures = []
    for m in metadata.members:
        if m.kind != "measure" or not m.normalized_sql or not m.coverage_sql:
            continue
        measures.append(
            _Measure(
                expression=sqlglot.parse_one(m.normalized_sql, read="snowflake"),
                inputs=_dedupe([_split(c) for c in m.numerator_columns + m.denominator_columns]),
                denominators=_dedupe([_split(c) for c in m.denominator_columns]),
                coverage=sqlglot.parse_one(m.coverage_sql, read="snowflake"),
            )
        )
    return measures


def _split(qualified: str) -> tuple[str, str]:
    table, _, column = qualified.partition(".")
    if not table or not column:
        raise ValueError(f"Expected TABLE.COLUMN, got {qualified!r}")
    return table, column


def _dedupe(items: list) -> list:
    return list(dict.fromkeys(items))


def _strip(node: exp.Expr) -> exp.Expr:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _is_query(node: exp.Expr) -> bool:
    return isinstance(node, (exp.Query, exp.Subquery, exp.With, exp.CTE))


def _local(expr: exp.Expr) -> list[exp.Expr]:
    """Nodes of ``expr`` in its own query level (nested queries are not entered)."""
    return [
        n
        for n in expr.walk(prune=lambda n: n is not expr and _is_query(n))
        if n is expr or not _is_query(n)
    ]


def _in_window(node: exp.Expr, stop: exp.Expr) -> bool:
    parent = node.parent
    while parent is not None and parent is not stop:
        if isinstance(parent, exp.Window):
            return True
        parent = parent.parent
    return isinstance(stop, exp.Window)


def _aggregates(expr: exp.Expr) -> list[exp.AggFunc]:
    """Aggregate calls of ``expr`` that aggregate rows (not window functions)."""
    return [n for n in _local(expr) if isinstance(n, exp.AggFunc) and not _in_window(n, expr)]


def _is_aggregating(select: exp.Select) -> bool:
    if select.args.get("group") is not None:
        return True
    parts = list(select.expressions)
    if select.args.get("having") is not None:
        parts.append(select.args["having"])
    return any(_aggregates(p) for p in parts)


def _windows(expr: exp.Expr) -> list[exp.Window]:
    return [n for n in _local(expr) if isinstance(n, exp.Window)]


def _is_value_window(window: exp.Window) -> bool:
    return not isinstance(window.this, _RANKING)


def _is_window_aggregate(window: exp.Window) -> bool:
    """SUM/AVG/COUNT/RATIO_TO_REPORT ... OVER: a value computed across many rows."""
    return isinstance(window.this, (exp.AggFunc, exp.Anonymous)) and not isinstance(
        window.this, (*_RANKING, exp.Lag, exp.Lead, exp.FirstValue, exp.LastValue)
    )


def _sources(scope: Scope) -> dict[str, exp.Table | Scope]:
    return {name: source for name, (_, source) in scope.selected_sources.items()}


def _reject(detail: str = "") -> QueryRejected:
    return QueryRejected(f"{detail} {_SINGLE_LEVEL}".strip())


def _alias(n: int, kind: str, input_column: str | None) -> str:
    suffix = f"_{re.sub(r'[^0-9A-Za-z_]', '_', input_column)}" if kind == "missing_input" else ""
    return f"{_PREFIX}{n}_{kind}{suffix}"


def _col(table: str, column: str) -> exp.Column:
    return exp.column(column, table=table, quoted=True)


def _count_if_null(columns: list[exp.Expr]) -> exp.Expr:
    predicate = " OR ".join(f"{_sql(c)} IS NULL" for c in columns)
    return _parse(f"COUNT_IF({predicate})")


def _sql(node: exp.Expr) -> str:
    return node.sql(dialect="snowflake", identify=True)


def _parse(sql: str) -> exp.Expr:
    return sqlglot.parse_one(sql, read="snowflake")


class _Augmenter:
    def __init__(self, measures: list[_Measure]):
        self.measures = measures
        self.levels: dict[int, _Level] = {}
        self.changed = False

    # ---- scopes that do not feed the result

    def check_unreachable(self, root: Scope) -> None:
        """Aggregates that do not feed the result rows may only filter, never be output."""
        reachable = self._reachable(root, set())
        for scope in root.traverse():
            if id(scope) in reachable or not isinstance(scope.expression, exp.Select):
                continue
            select = scope.expression
            if _is_aggregating(select) and any(
                self._aggregated(src) for src in _sources(scope).values()
            ):
                raise _reject("An aggregated result is aggregated again.")
            if scope.is_subquery and _is_aggregating(select) and not self._in_filter(select):
                raise _reject("A subquery computes an aggregate inside the select list.")

    def _reachable(self, scope: Scope, seen: set[int]) -> set[int]:
        seen.add(id(scope))
        for source in _sources(scope).values():
            if isinstance(source, Scope) and id(source) not in seen:
                self._reachable(source, seen)
        return seen

    @staticmethod
    def _in_filter(select: exp.Select) -> bool:
        node = select.parent
        while node is not None and not isinstance(node, exp.Select):
            if isinstance(node, _FILTER_CLAUSES):
                return True
            node = node.parent
        return False

    def _aggregated(self, source: exp.Table | Scope) -> bool:
        if not isinstance(source, Scope) or not isinstance(source.expression, exp.Select):
            return False
        return _is_aggregating(source.expression) or any(
            self._aggregated(s) for s in _sources(source).values()
        )

    # ---- scopes that feed the result

    def level(self, scope: Scope) -> _Level:
        key = id(scope)
        if key not in self.levels:
            self.levels[key] = self._build(scope)
        return self.levels[key]

    def _build(self, scope: Scope) -> _Level:
        select = scope.expression
        if not isinstance(select, exp.Select):
            raise _reject("Only plain SELECT query blocks are supported.")
        sources = _sources(scope)
        children = {
            name: self.level(src) for name, src in sources.items() if isinstance(src, Scope)
        }
        aggregated_children = {n: lvl for n, lvl in children.items() if lvl.aggregated}
        if _is_aggregating(select):
            if aggregated_children:
                raise _reject("An aggregated result is aggregated again.")
            return self._aggregate_level(scope, select)
        if aggregated_children:
            if len(sources) > 1:
                raise _reject("Aggregated rows are joined.")
            ((name, child),) = aggregated_children.items()
            return self._carry_level(select, name, child)
        self._reject_window_aggregates(select)
        return _Level(False, {}, [e.alias_or_name for e in select.expressions])

    # ---- aggregation level

    def _aggregate_level(self, scope: Scope, select: exp.Select) -> _Level:
        outputs = [e.alias_or_name for e in select.expressions]
        carries: dict[str, list[_Spec]] = {}
        added: list[exp.Expr] = []
        for n, projection in enumerate(select.expressions):
            expr = projection.this if isinstance(projection, exp.Alias) else projection
            for window in _windows(expr):
                if _is_value_window(window):
                    raise _reject("A window function is applied to aggregated values.")
            if not _aggregates(expr):
                continue
            specs: list[_Spec] = []
            for kind, input_column, companion in self._companions(scope, select, expr):
                alias = _alias(n, kind, input_column)
                specs.append(_Spec(kind, input_column, alias))
                added.append(exp.alias_(companion, alias, quoted=True))
            carries[projection.alias_or_name] = specs
        if added:
            if select.args.get("distinct"):
                raise _reject("SELECT DISTINCT cannot be combined with completeness columns.")
            select.set("expressions", [*select.expressions, *added])
            self.changed = True
        return _Level(True, carries, outputs)

    def _companions(
        self, scope: Scope, select: exp.Select, expr: exp.Expr
    ) -> list[tuple[CompanionKind, str | None, exp.Expr]]:
        result: list[tuple[CompanionKind, str | None, exp.Expr]] = [
            ("block_groups", None, _parse("COUNT(*)"))
        ]
        core = _core(expr)
        measure = next(
            (m for m in self.measures if m.expression == expr or m.expression == core), None
        )
        if measure is not None:
            names = _input_names(measure.inputs)
            for (table, column), name in zip(measure.inputs, names, strict=True):
                result.append(("missing_input", name, _count_if_null([_col(table, column)])))
            result.append(("coverage", None, measure.coverage.copy()))
            dens = [_col(t, c) for t, c in measure.denominators]
            result.append(("unknown_denominator", None, _count_if_null(dens)))
            return result

        moe = _moe_column(core)
        if moe is not None:
            result.append(("missing_moe", self._name(scope, moe), _count_if_null([moe])))
            return result

        inputs = self._inputs(select, expr)
        for column, name in zip(inputs, self._names(scope, inputs), strict=True):
            result.append(("missing_input", name, _count_if_null([column])))
        rate = _custom_rate(core)
        if rate is not None:
            part, whole = rate
            rate_inputs = _dedupe_columns(
                [c for c in [*part.find_all(exp.Column), *whole.find_all(exp.Column)]]
            )
            valid = " OR ".join(f"{_sql(c)} IS NULL" for c in rate_inputs)
            whole_sql = _sql(whole)
            coverage = f"SUM(IFF({valid}, NULL, {whole_sql})) / NULLIF(SUM({whole_sql}), 0)"
            result.append(("coverage", None, _parse(coverage)))
            dens = _dedupe_columns(list(whole.find_all(exp.Column)))
            result.append(("unknown_denominator", None, _count_if_null(dens)))
        return result

    def _inputs(self, select: exp.Select, expr: exp.Expr) -> list[exp.Column]:
        """Column references inside the aggregates of ``expr`` (lateral aliases resolved)."""
        columns: list[exp.Column] = []
        for agg in _aggregates(expr):
            for col in agg.find_all(exp.Column):
                if isinstance(col.this, exp.Star):
                    continue
                if not col.table:  # lateral reference to another select item
                    target = _lateral(select, col.name)
                    if target is None:
                        raise _reject(f'Column "{col.name}" cannot be traced.')
                    columns.extend(c for c in target.find_all(exp.Column) if c.table)
                    continue
                columns.append(col)
        return _dedupe_columns(columns)

    def _names(self, scope: Scope, columns: list[exp.Column]) -> list[str]:
        names = [self._name(scope, c) for c in columns]
        qualified = [f"{c.table}.{n}" for c, n in zip(columns, names, strict=True)]
        return [q if names.count(n) > 1 else n for n, q in zip(names, qualified, strict=True)]

    def _name(self, scope: Scope, col: exp.Column) -> str:
        """Physical column name behind ``col`` when it is a plain pass-through, else its name."""
        source = _sources(scope).get(col.table)
        if isinstance(source, Scope) and isinstance(source.expression, exp.Select):
            matches = [e for e in source.expression.expressions if e.alias_or_name == col.name]
            if len(matches) == 1:
                inner = matches[0].this if isinstance(matches[0], exp.Alias) else matches[0]
                if isinstance(inner, exp.Column) and inner.table:
                    return self._name(source, inner)
        return col.name

    # ---- pass-through level

    def _carry_level(self, select: exp.Select, source: str, child: _Level) -> _Level:
        self._expand_stars(select, source, child)
        outputs = [e.alias_or_name for e in select.expressions]
        carries: dict[str, list[_Spec]] = {}
        added: list[exp.Expr] = []
        for n, projection in enumerate(select.expressions):
            refs = self._value_refs(select, projection, source, child, set())
            if len(refs) > 1:
                raise _reject("An output combines several aggregated values.")
            if not refs:
                continue
            specs: list[_Spec] = []
            for spec in child.carries[refs.pop()]:
                alias = _alias(n, spec.kind, spec.input_column)
                specs.append(_Spec(spec.kind, spec.input_column, alias))
                added.append(exp.alias_(_col(source, spec.alias), alias, quoted=True))
            carries[projection.alias_or_name] = specs
        if added:
            if select.args.get("distinct"):
                raise _reject("SELECT DISTINCT cannot be combined with completeness columns.")
            select.set("expressions", [*select.expressions, *added])
            self.changed = True
        return _Level(True, carries, outputs)

    @staticmethod
    def _expand_stars(select: exp.Select, source: str, child: _Level) -> None:
        expanded: list[exp.Expr] = []
        for e in select.expressions:
            star = isinstance(e, exp.Star) or (
                isinstance(e, exp.Column) and isinstance(e.this, exp.Star)
            )
            if star and (isinstance(e, exp.Star) or e.table == source):
                expanded.extend(_col(source, name) for name in child.outputs)
            elif star:
                raise _reject("SELECT * cannot be traced here.")
            else:
                expanded.append(e)
        select.set("expressions", expanded)

    def _value_refs(
        self, select: exp.Select, expr: exp.Expr, source: str, child: _Level, seen: set[str]
    ) -> set[str]:
        """Carried columns whose value flows into ``expr`` (window ordering keys excluded)."""
        refs: set[str] = set()
        for window in _windows(expr):
            if _is_value_window(window) and (
                _is_window_aggregate(window) or self._mentions(select, window, source, child, seen)
            ):
                raise _reject("A window function is applied to aggregated values.")
        for col in _local(expr):
            if not isinstance(col, exp.Column) or _in_window(col, expr):
                continue
            if col.table == source and col.name in child.carries:
                refs.add(col.name)
            elif not col.table and col.name not in seen:
                target = _lateral(select, col.name)
                if target is not None:
                    refs |= self._value_refs(select, target, source, child, seen | {col.name})
        return refs

    def _mentions(
        self, select: exp.Select, expr: exp.Expr, source: str, child: _Level, seen: set[str]
    ) -> bool:
        for col in expr.find_all(exp.Column):
            if col.table == source and col.name in child.carries:
                return True
            if not col.table and col.name not in seen:
                target = _lateral(select, col.name)
                if target is not None and self._mentions(
                    select, target, source, child, seen | {col.name}
                ):
                    return True
        return False

    # ---- raw block-group level

    @staticmethod
    def _reject_window_aggregates(select: exp.Select) -> None:
        for projection in select.expressions:
            for window in _windows(projection):
                if _is_window_aggregate(window):
                    raise _reject("A window aggregate across block groups has no completeness.")


def _lateral(select: exp.Select, name: str) -> exp.Expr | None:
    for e in select.expressions:
        if isinstance(e, exp.Alias) and e.alias == name:
            return e.this
    return None


def _dedupe_columns(columns: list[exp.Column]) -> list[exp.Column]:
    seen: dict[tuple[str, str], exp.Column] = {}
    for c in columns:
        seen.setdefault((c.table, c.name), c)
    return list(seen.values())


def _input_names(inputs: list[tuple[str, str]]) -> list[str]:
    names = [c for _, c in inputs]
    return [c if names.count(c) == 1 else f"{t}.{c}" for t, c in inputs]


def _core(expr: exp.Expr) -> exp.Expr:
    """``expr`` without surrounding parentheses or ROUND(...)."""
    node = _strip(expr)
    while isinstance(node, exp.Round):
        node = _strip(node.this)
    return node


def _unscale(node: exp.Expr) -> exp.Expr:
    node = _strip(node)
    while isinstance(node, exp.Mul):
        left, right = _strip(node.this), _strip(node.expression)
        if isinstance(left, exp.Literal) and not left.is_string:
            node = right
        elif isinstance(right, exp.Literal) and not right.is_string:
            node = left
        else:
            break
    return _strip(node)


def _custom_rate(core: exp.Expr) -> tuple[exp.Expr, exp.Expr] | None:
    """``(part, whole)`` for ``[scale *] SUM(part) / [NULLIF(]SUM(whole)[, 0)]``."""
    div = _unscale(core)
    if not isinstance(div, exp.Div):
        return None
    num, den = _unscale(div.this), _strip(div.expression)
    if isinstance(den, exp.Nullif):
        zero = _strip(den.expression)
        if not (isinstance(zero, exp.Literal) and zero.name == "0"):
            return None
        den = _strip(den.this)
    if not (isinstance(num, exp.Sum) and isinstance(den, exp.Sum)):
        return None
    part, whole = num.this, den.this
    if any(isinstance(n, (exp.AggFunc, exp.Window)) for n in (*part.walk(), *whole.walk())):
        return None
    return part, whole


def _moe_column(core: exp.Expr) -> exp.Column | None:
    """``moe`` for ``SQRT(SUM(POWER(moe, 2)))``."""
    if not isinstance(core, exp.Sqrt):
        return None
    total = _strip(core.this)
    if not isinstance(total, exp.Sum):
        return None
    power = _strip(total.this)
    if not isinstance(power, exp.Pow):
        return None
    base, exponent = _strip(power.this), _strip(power.expression)
    if isinstance(base, exp.Column) and isinstance(exponent, exp.Literal) and exponent.name == "2":
        return base
    return None


# --------------------------------------------------------------------------- split_result


def split_result(
    columns: Sequence[str],
    rows: Sequence[Sequence[Any] | Mapping[str, Any]],
    companions: Sequence[Companion],
) -> tuple[list[dict], list[Completeness]]:
    """Remove companion columns from ``rows``; one ``Completeness`` per row and aggregate."""
    index = {name: i for i, name in enumerate(columns)}
    missing = [c.alias for c in companions if c.alias not in index]
    if missing:
        raise ValueError(f"Result is missing completeness columns: {', '.join(missing)}")
    hidden = {c.alias for c in companions}
    by_output: dict[str, list[Companion]] = {}
    for c in companions:
        by_output.setdefault(c.output_column, []).append(c)

    out_rows: list[dict] = []
    completeness: list[Completeness] = []
    for r, row in enumerate(rows):
        values = [row[name] for name in columns] if isinstance(row, Mapping) else list(row)
        out_rows.append({name: values[i] for name, i in index.items() if name not in hidden})
        for column, items in by_output.items():
            fields: dict[str, Any] = {"row": r, "column": column, "missing_inputs": {}}
            for c in items:
                value = values[index[c.alias]]
                if c.kind == "coverage":
                    fields["coverage"] = None if value is None else float(value)
                    continue
                if value is None and c.kind in ("block_groups", "missing_input"):
                    raise ValueError(f"Completeness count {c.alias} is NULL in row {r}.")
                count = None if value is None else int(value)
                if c.kind == "missing_input":
                    fields["missing_inputs"][c.input_column] = count
                else:
                    fields[c.kind] = count
            if "block_groups" not in fields:
                raise ValueError(f"No block_groups companion for output column {column!r}.")
            completeness.append(Completeness(**fields))
    return out_rows, completeness
