from decimal import Decimal

import pytest
import sqlglot
from sqlglot import exp

from census_compass.contracts import BuilderMetadata, MemberMetadata, QueryRejected
from census_compass.database.completeness import (
    AugmentedQuery,
    Companion,
    augment,
    normalize_measure_sql,
    split_result,
)
from census_compass.database.validation import validate_and_fix

DB = "CENSUS"
SCHEMA = "PUBLIC"
FIPS = "2020_METADATA_CBG_FIPS_CODES"
B23 = "2020_CBG_B23"

E5, E3 = f'"{B23}"."B23025e5"', f'"{B23}"."B23025e3"'
_VALID = f"{E5} IS NULL OR {E3} IS NULL"
UNEMPLOYMENT_MEASURE_SQL = f"""
100 * SUM(IFF({_VALID}, NULL, {E5}))
  / NULLIF(SUM(IFF({_VALID},
                   NULL, {E3})), 0)
"""
UNEMPLOYMENT_COVERAGE_SQL = f"SUM(IFF({_VALID}, NULL, {E3})) / NULLIF(SUM({E3}), 0)"


def _member(table, column, kind, **extra):
    return MemberMetadata(
        file="f",
        member=extra.pop("member", f"{table}_{column}".lower()),
        kind=kind,
        table=table,
        source_column=column,
        aggregation=extra.pop("aggregation", "sum" if kind == "measure" else "none"),
        definition_source=extra.pop("definition_source", "dictionary"),
        **extra,
    )


META = BuilderMetadata(
    year=2020,
    members=[
        _member("2020_CBG_B01", "CENSUS_BLOCK_GROUP", "dimension"),
        _member("2020_CBG_B01", "B01001e1", "measure"),
        _member("2020_CBG_B19", "B19001e1", "measure"),
        _member("2020_CBG_B19", "B19013e1", "dimension"),
        _member(B23, "B23025e3", "measure"),
        _member(B23, "B23025e5", "measure"),
        _member(FIPS, "STATE", "dimension"),
        _member(FIPS, "COUNTY", "dimension"),
        _member(
            B23,
            None,
            "measure",
            member="unemployment_rate",
            aggregation="formula",
            definition_source="hand_written",
            normalized_sql=normalize_measure_sql(UNEMPLOYMENT_MEASURE_SQL),
            numerator_columns=[f"{B23}.B23025e5"],
            denominator_columns=[f"{B23}.B23025e3"],
            coverage_sql=UNEMPLOYMENT_COVERAGE_SQL,
        ),
    ],
    table_columns={
        "2020_CBG_B01": ["CENSUS_BLOCK_GROUP", "B01001e1", "B01001m1"],
        "2020_CBG_B19": ["CENSUS_BLOCK_GROUP", "B19001e1", "B19013e1"],
        B23: ["CENSUS_BLOCK_GROUP", "B23025e3", "B23025e5", "B23025m5"],
        FIPS: ["STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"],
    },
    fips_table=FIPS,
)
ALLOWED = ["2020_CBG_B01", "2020_CBG_B19", B23, FIPS]

FIPS_JOIN = (
    f'JOIN "{FIPS}" f ON LEFT(e."CENSUS_BLOCK_GROUP", 5) = f."STATE_FIPS" || f."COUNTY_FIPS"'
)
TOTAL_POP = 'SELECT SUM(p."B01001e1") AS total_pop FROM "2020_CBG_B01" p'
BY_COUNTY = (
    f'SELECT f."COUNTY", SUM(e."B23025e5") AS unemployed FROM "{B23}" e {FIPS_JOIN} '
    'GROUP BY f."COUNTY"'
)
UNEMPLOYMENT = (
    'SELECT 100 * sum(iff(e."B23025e5" is null or e."B23025e3" is null, null, e."B23025e5"))'
    ' / nullif(sum(iff(e."B23025e5" is null or e."B23025e3" is null, null, e."B23025e3")), 0)'
    f' AS unemployment_rate FROM "{B23}" e'
)
CUSTOM_RATE = (
    f'SELECT f."COUNTY", 100 * SUM(e."B23025e5") / NULLIF(SUM(e."B23025e3"), 0) AS rate '
    f'FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY"'
)
MOE = f'SELECT SQRT(SUM(POWER(e."B23025m5", 2))) AS moe FROM "{B23}" e'
RANK_TOP10 = f"""
WITH county_rates AS (
  SELECT f."STATE", f."COUNTY",
         SUM(e."B23025e5") / NULLIF(SUM(e."B23025e3"), 0) AS rate
  FROM "{B23}" e {FIPS_JOIN}
  GROUP BY f."STATE", f."COUNTY"
), ranked AS (
  SELECT "STATE", "COUNTY", rate, RANK() OVER (ORDER BY rate DESC) AS rnk
  FROM county_rates
)
SELECT "STATE", "COUNTY", rate, rnk FROM ranked WHERE rnk <= 10 ORDER BY rnk
"""


def _v(sql: str) -> str:
    return validate_and_fix(sql, META, ALLOWED, 1000, DB, SCHEMA)


def _aug(sql: str) -> AugmentedQuery:
    q = augment(_v(sql), META)
    if q.companions:
        assert _v(q.sql) == q.sql  # still passes every safety rule, unchanged
    return q


def _kinds(q: AugmentedQuery, column: str | None = None) -> set[str]:
    return {c.kind for c in q.companions if column is None or c.output_column == column}


def _projection_aliases(select: exp.Select) -> list[str]:
    return [e.alias_or_name for e in select.expressions]


def _rejected(sql: str) -> str:
    validated = _v(sql)
    with pytest.raises(QueryRejected) as e:
        augment(validated, META)
    return e.value.reason.lower()


# --------------------------------------------------------------------------- normalization


def test_normalize_measure_sql_is_canonical_and_idempotent():
    once = normalize_measure_sql(UNEMPLOYMENT_MEASURE_SQL)
    assert "\n" not in once and normalize_measure_sql(once) == once


# --------------------------------------------------------------------------- totals


def test_total_gets_block_groups_and_missing():
    q = _aug(TOTAL_POP)
    assert _kinds(q) == {"block_groups", "missing_input"}
    assert {c.output_column for c in q.companions} == {"TOTAL_POP"}
    missing = [c for c in q.companions if c.kind == "missing_input"]
    assert [c.input_column for c in missing] == ["B01001e1"]
    assert 'COUNT_IF("2020_CBG_B01"."B01001e1" IS NULL)' in q.sql
    assert all(c.alias.startswith("__cc_0_") for c in q.companions)


def test_count_star_gets_block_groups_only():
    q = _aug('SELECT COUNT(*) AS n FROM "2020_CBG_B01"')
    assert [c.kind for c in q.companions] == ["block_groups"]


def test_grouped_query_companions_share_group_by():
    q = _aug(BY_COUNTY)
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    assert tree.args["group"] is not None
    assert set(_projection_aliases(tree)) >= {c.alias for c in q.companions}
    assert _kinds(q, "UNEMPLOYED") == {"block_groups", "missing_input"}
    assert not _kinds(q, "COUNTY")  # group keys get nothing


def test_group_by_position_still_points_at_the_same_column():
    q = _aug(f'SELECT f."COUNTY", SUM(e."B23025e5") FROM "{B23}" e {FIPS_JOIN} GROUP BY 1')
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    assert tree.expressions[0].sql(dialect="snowflake") == f'"{FIPS}"."COUNTY"'


# --------------------------------------------------------------------------- ratios


def test_hand_written_ratio_gets_coverage():
    q = _aug(UNEMPLOYMENT)
    assert _kinds(q) == {"block_groups", "missing_input", "coverage", "unknown_denominator"}
    assert {c.input_column for c in q.companions if c.kind == "missing_input"} == {
        "B23025e5",
        "B23025e3",
    }
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    by_alias = {e.alias: e.this for e in tree.expressions}
    coverage = next(c for c in q.companions if c.kind == "coverage")
    assert by_alias[coverage.alias] == sqlglot.parse_one(
        UNEMPLOYMENT_COVERAGE_SQL, read="snowflake"
    )
    unknown = next(c for c in q.companions if c.kind == "unknown_denominator")
    assert by_alias[unknown.alias].sql(dialect="snowflake") == f"COUNT_IF({E3} IS NULL)"


def test_hand_written_ratio_matches_when_rounded():
    sql = UNEMPLOYMENT.replace("SELECT 100", "SELECT ROUND(100").replace(
        ") AS unemployment_rate", "), 1) AS unemployment_rate"
    )
    q = _aug(sql)
    coverage = next(c for c in q.companions if c.kind == "coverage")
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    by_alias = {e.alias: e.this for e in tree.expressions}
    assert by_alias[coverage.alias] == sqlglot.parse_one(
        UNEMPLOYMENT_COVERAGE_SQL, read="snowflake"
    )


def test_custom_rate_gets_derived_coverage():
    q = _aug(CUSTOM_RATE)
    assert _kinds(q, "RATE") == {
        "block_groups",
        "missing_input",
        "coverage",
        "unknown_denominator",
    }
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    by_alias = {e.alias: e.this for e in tree.expressions}
    coverage = next(c for c in q.companions if c.kind == "coverage")
    expected = sqlglot.parse_one(
        f"SUM(IFF({E5} IS NULL OR {E3} IS NULL, NULL, {E3})) / NULLIF(SUM({E3}), 0)",
        read="snowflake",
    )
    assert by_alias[coverage.alias] == expected


def test_moe_sum_gets_missing_moe():
    q = _aug(MOE)
    assert "missing_moe" in _kinds(q)
    moe = next(c for c in q.companions if c.kind == "missing_moe")
    assert moe.input_column == "B23025m5"


# --------------------------------------------------------------------------- outer levels


def test_rank_top10_carries_companions():
    q = _aug(RANK_TOP10)
    assert _kinds(q, "RATE") == {"block_groups", "missing_input", "coverage", "unknown_denominator"}
    assert not _kinds(q, "RNK") and not _kinds(q, "STATE")
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    root_aliases = set(_projection_aliases(tree))
    assert {c.alias for c in q.companions} <= root_aliases
    assert all(c.alias.startswith("__cc_2_") for c in q.companions)  # RATE is output column 2
    assert tree.args["where"] is not None and tree.args["order"] is not None
    # companions are computed in the aggregating CTE, next to its GROUP BY
    cte = next(c for c in tree.find_all(exp.CTE) if c.alias == "COUNTY_RATES")
    assert cte.this.args["group"] is not None
    assert any(a.startswith("__cc_") for a in _projection_aliases(cte.this))


def test_outer_rename_and_round_inherit_companions():
    q = _aug(
        f'WITH t AS ({BY_COUNTY}) SELECT t."COUNTY" AS name, ROUND(t.unemployed, 0) AS u '
        "FROM t ORDER BY u DESC LIMIT 5"
    )
    assert _kinds(q, "U") == {"block_groups", "missing_input"}
    assert {c.alias for c in q.companions if c.output_column == "U"} == {
        "__cc_1_block_groups",
        "__cc_1_missing_input_B23025e5",
    }


def test_star_over_aggregated_cte_is_expanded_and_carried():
    q = _aug(f"WITH t AS ({BY_COUNTY}) SELECT * FROM t ORDER BY unemployed DESC")
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    assert _projection_aliases(tree)[:2] == ["COUNTY", "UNEMPLOYED"]
    assert _kinds(q, "UNEMPLOYED") == {"block_groups", "missing_input"}


def test_derived_table_input_named_by_physical_column():
    q = _aug(
        'SELECT SUM(s.pop) AS total FROM (SELECT "2020_CBG_B01"."B01001e1" AS pop '
        'FROM "2020_CBG_B01" WHERE "2020_CBG_B01"."B01001e1" > 0) s'
    )
    missing = [c for c in q.companions if c.kind == "missing_input"]
    assert [c.input_column for c in missing] == ["B01001e1"]


def test_reaggregation_rejected():
    reason = _rejected(f"SELECT AVG(s.rate) FROM ({CUSTOM_RATE}) s")
    assert "single-level aggregation" in reason


@pytest.mark.parametrize(
    "sql",
    [
        f"WITH t AS ({BY_COUNTY}) SELECT COUNT(*) FROM t",
        f'WITH t AS (SELECT f."COUNTY" FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY") '
        "SELECT COUNT(*) FROM t",
        f'WITH t AS ({BY_COUNTY}) SELECT t."COUNTY", t.unemployed / SUM(t.unemployed) OVER () '
        "AS share FROM t",
        f'WITH t AS ({BY_COUNTY}) SELECT t."COUNTY", LAG(t.unemployed) OVER '
        '(ORDER BY t."COUNTY") AS prev FROM t',
        f'WITH t AS (SELECT f."COUNTY", SUM(e."B23025e5") AS u, SUM(e."B23025e3") AS lf '
        f'FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY") SELECT t.u / t.lf AS r FROM t',
        f"WITH t AS ({BY_COUNTY}) SELECT DISTINCT t.unemployed FROM t",
        f'SELECT f."COUNTY", SUM(e."B23025e5") AS u, RATIO_TO_REPORT(SUM(e."B23025e5")) OVER () '
        f'AS share FROM "{B23}" e {FIPS_JOIN} GROUP BY f."COUNTY"',
        'SELECT "2020_CBG_B19"."CENSUS_BLOCK_GROUP", (SELECT SUM("2020_CBG_B01"."B01001e1") '
        'FROM "2020_CBG_B01") AS total FROM "2020_CBG_B19"',
        'SELECT "2020_CBG_B01"."CENSUS_BLOCK_GROUP", SUM("2020_CBG_B01"."B01001e1") OVER () AS t '
        'FROM "2020_CBG_B01"',
    ],
    ids=[
        "count_over_aggregate",
        "count_over_grouped_keys",
        "window_sum_over_aggregate",
        "lag_over_aggregate",
        "outer_arithmetic_of_two_aggregates",
        "distinct_over_aggregate",
        "ratio_to_report_of_aggregate",
        "scalar_subquery_aggregate_in_projection",
        "window_aggregate_over_block_groups",
    ],
)
def test_uncarriable_queries_rejected(sql):
    assert "single-level aggregation" in _rejected(sql)


def test_existing_companion_prefix_rejected():
    with pytest.raises(QueryRejected):
        augment(_v('SELECT SUM("2020_CBG_B01"."B01001e1") AS "__cc_0_x" FROM "2020_CBG_B01"'), META)


# --------------------------------------------------------------------------- non-aggregates


@pytest.mark.parametrize(
    "sql",
    [
        'SELECT "2020_CBG_B01"."B01001e1" FROM "2020_CBG_B01" '
        'WHERE "2020_CBG_B01"."CENSUS_BLOCK_GROUP" = \'170310101001\'',
        f'SELECT "{FIPS}"."STATE", "{FIPS}"."COUNTY" FROM "{FIPS}"',
        'SELECT "2020_CBG_B01"."CENSUS_BLOCK_GROUP", RANK() OVER (ORDER BY '
        '"2020_CBG_B01"."B01001e1" DESC) AS r FROM "2020_CBG_B01"',
        'SELECT "2020_CBG_B19"."CENSUS_BLOCK_GROUP" FROM "2020_CBG_B19" WHERE '
        '"2020_CBG_B19"."B19001e1" > (SELECT AVG("2020_CBG_B01"."B01001e1") FROM "2020_CBG_B01")',
    ],
    ids=["block_group_lookup", "dimensions", "block_group_rank", "aggregate_only_in_filter"],
)
def test_non_aggregate_query_unchanged(sql):
    validated = _v(sql)
    q = augment(validated, META)
    assert q.sql == validated and q.companions == []


def test_filter_on_aggregate_without_aggregate_output_gets_nothing():
    q = _aug(f'WITH t AS ({BY_COUNTY}) SELECT t."COUNTY" FROM t WHERE t.unemployed > 100')
    assert q.companions == []


# --------------------------------------------------------------------------- split_result

COMPANIONS = [
    Companion("__cc_1_block_groups", "RATE", "block_groups", None),
    Companion("__cc_1_missing_input_B23025e5", "RATE", "missing_input", "B23025e5"),
    Companion("__cc_1_missing_input_B23025e3", "RATE", "missing_input", "B23025e3"),
    Companion("__cc_1_coverage", "RATE", "coverage", None),
    Companion("__cc_1_unknown_denominator", "RATE", "unknown_denominator", None),
    Companion("__cc_2_block_groups", "MOE", "block_groups", None),
    Companion("__cc_2_missing_moe", "MOE", "missing_moe", "B23025m5"),
]
COLUMNS = ["COUNTY", "RATE", "MOE"] + [c.alias for c in COMPANIONS]


def test_split_removes_companions():
    rows, comp = split_result(
        COLUMNS,
        [
            ("Cook", 7.1, 12.0, 4002, 0, 3, Decimal("0.99"), 1, 4002, 2),
            ("Lake", None, None, 10, 0, 0, None, 0, 10, 0),
        ],
        COMPANIONS,
    )
    assert rows == [
        {"COUNTY": "Cook", "RATE": 7.1, "MOE": 12.0},
        {"COUNTY": "Lake", "RATE": None, "MOE": None},
    ]
    assert [(c.row, c.column) for c in comp] == [(0, "RATE"), (0, "MOE"), (1, "RATE"), (1, "MOE")]
    first = comp[0]
    assert first.block_groups == 4002 and first.missing_inputs == {"B23025e5": 0, "B23025e3": 3}
    assert first.coverage == pytest.approx(0.99) and isinstance(first.coverage, float)
    assert first.unknown_denominator == 1 and first.missing_moe is None
    assert comp[1].missing_moe == 2 and comp[1].coverage is None and comp[1].missing_inputs == {}
    assert comp[2].coverage is None


def test_split_without_companions_returns_rows():
    rows, comp = split_result(["A"], [(1,), (2,)], [])
    assert rows == [{"A": 1}, {"A": 2}] and comp == []


def test_split_missing_companion_column_raises():
    with pytest.raises(ValueError):
        split_result(["RATE"], [(1.0,)], COMPANIONS[:1])


def test_split_null_block_groups_raises():
    with pytest.raises(ValueError):
        split_result(["RATE", "__cc_1_block_groups"], [(1.0, None)], COMPANIONS[:1])


def test_split_end_to_end_with_augmented_query():
    q = _aug(RANK_TOP10)
    tree = sqlglot.parse_one(q.sql, read="snowflake")
    columns = _projection_aliases(tree)
    values = {"STATE": "IL", "COUNTY": "Cook", "RATE": 0.07, "RNK": 1}
    row = tuple(values.get(c, 0 if "coverage" not in c else 1.0) for c in columns)
    rows, comp = split_result(columns, [row], q.companions)
    assert rows == [values]
    assert len(comp) == 1 and comp[0].column == "RATE" and comp[0].coverage == 1.0
    assert set(comp[0].missing_inputs) == {"B23025e5", "B23025e3"}
