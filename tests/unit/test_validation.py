import pytest

from census_compass.contracts import BuilderMetadata, MemberMetadata, QueryRejected
from census_compass.database.validation import validate_and_fix

DB = "CENSUS"
SCHEMA = "PUBLIC"
FIPS = "2020_METADATA_CBG_FIPS_CODES"


def _member(table, column, kind, aggregation="sum"):
    return MemberMetadata(
        file="f",
        member=f"{table}_{column}".lower(),
        kind=kind,
        table=table,
        source_column=column,
        aggregation=aggregation if kind == "measure" else "none",
        definition_source="dictionary",
    )


META = BuilderMetadata(
    year=2020,
    members=[
        _member("2020_CBG_B01", "CENSUS_BLOCK_GROUP", "dimension"),
        _member("2020_CBG_B01", "B01001e1", "measure"),
        _member("2020_CBG_B01", "B01003e1", "measure"),
        _member("2020_CBG_B19", "B19001e1", "measure"),
        _member("2020_CBG_B19", "B19013e1", "dimension"),  # median household income
        _member("2020_CBG_B19", "B19313e1", "measure"),
        _member("2020_CBG_B23", "B23025e1", "measure"),
        _member("2020_CBG_B23", "B23025e3", "measure"),
        _member("2020_CBG_B23", "B23025e5", "measure"),
        _member(FIPS, "STATE", "dimension"),
        _member(FIPS, "COUNTY", "dimension"),
        _member("2020_REDISTRICTING_CBG_DATA", "P0010001", "measure"),
    ],
    table_columns={
        "2020_CBG_B01": ["CENSUS_BLOCK_GROUP", "B01001e1", "B01003e1"],
        "2020_CBG_B19": ["CENSUS_BLOCK_GROUP", "B19001e1", "B19013e1", "B19313e1"],
        "2020_CBG_B23": ["CENSUS_BLOCK_GROUP", "B23025e1", "B23025e3", "B23025e5"],
        FIPS: ["STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"],
        "2020_REDISTRICTING_CBG_DATA": ["CENSUS_BLOCK_GROUP", "P0010001"],
        "2019_CBG_B01": ["CENSUS_BLOCK_GROUP", "B01001e1", "B01003e1"],
        "2019_METADATA_CBG_FIPS_CODES": ["STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"],
    },
    fips_table=FIPS,
)
ALLOWED = [
    "2020_CBG_B01",
    "2020_CBG_B19",
    "2020_CBG_B23",
    FIPS,
    "2020_REDISTRICTING_CBG_DATA",
    "2019_CBG_B01",
    "2019_METADATA_CBG_FIPS_CODES",
]

FIPS_ON = f'LEFT("2020_CBG_B23"."CENSUS_BLOCK_GROUP", 5) = "{FIPS}"."STATE_FIPS" || "{FIPS}"."COUNTY_FIPS"'

RANK_TOP10_UNEMPLOYMENT_SQL = """
WITH county_rates AS (
  SELECT f."STATE", f."COUNTY",
         SUM(e."B23025e5") / NULLIF(SUM(e."B23025e3"), 0) AS rate
  FROM "2020_CBG_B23" e
  JOIN "2020_METADATA_CBG_FIPS_CODES" f
    ON LEFT(e."CENSUS_BLOCK_GROUP", 5) = f."STATE_FIPS" || f."COUNTY_FIPS"
  GROUP BY f."STATE", f."COUNTY"
), ranked AS (
  SELECT "STATE", "COUNTY", rate, RANK() OVER (ORDER BY rate DESC) AS rnk
  FROM county_rates
)
SELECT "STATE", "COUNTY", rate, rnk FROM ranked WHERE rnk <= 10 ORDER BY rnk
"""

UNKNOWN_COL_TYPO_SQL = 'SELECT SUM("2020_CBG_B01"."B01001e") FROM "2020_CBG_B01"'

REJECT = {
    "multi": "SELECT 1; SELECT 2",
    "dml": 'DELETE FROM "2020_CBG_B01"',
    "excluded": 'SELECT * FROM "2020_CBG_B99"',
    "unknown_col": 'SELECT "2020_CBG_B01"."B01001e999" FROM "2020_CBG_B01"',
    "mix_years": 'SELECT 1 FROM "2019_CBG_B01" JOIN "2020_CBG_B01" ON '
    '"2019_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"',
    "mix_sources": 'SELECT 1 FROM "2020_REDISTRICTING_CBG_DATA" JOIN "2020_CBG_B01" ON '
    '"2020_REDISTRICTING_CBG_DATA"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"',
    "bad_join": 'SELECT 1 FROM "2020_CBG_B01" JOIN "2020_CBG_B19" ON '
    '"2020_CBG_B01"."B01001e1" = "2020_CBG_B19"."B19001e1"',
    "avg_median": 'SELECT AVG("2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
    "cte_median": 'WITH x AS (SELECT "2020_CBG_B19"."B19013e1" AS v FROM "2020_CBG_B19") '
    "SELECT AVG(v) FROM x",
    "window_median": 'SELECT SUM("2020_CBG_B19"."B19013e1") OVER () FROM "2020_CBG_B19"',
    "self_join": 'SELECT 1 FROM "2020_CBG_B01" a JOIN "2020_CBG_B01" b '
    'ON a."CENSUS_BLOCK_GROUP" = b."CENSUS_BLOCK_GROUP"',
    "untraceable": 'SELECT SUM(v) FROM (SELECT * FROM "2020_CBG_B19") t',
    # statement shape and bypass attempts
    "unparseable": "SELEC 1 FROM",
    "empty": "   ",
    "insert": 'INSERT INTO "2020_CBG_B01" SELECT * FROM "2020_CBG_B01"',
    "create": 'CREATE TABLE x AS SELECT * FROM "2020_CBG_B01"',
    "union": 'SELECT "2020_CBG_B01"."B01001e1" FROM "2020_CBG_B01" UNION ALL '
    'SELECT "2020_CBG_B19"."B19001e1" FROM "2020_CBG_B19"',
    "select_into": 'SELECT "2020_CBG_B01"."B01001e1" INTO x FROM "2020_CBG_B01"',
    "lateral_flatten": 'SELECT 1 FROM "2020_CBG_B01", LATERAL FLATTEN(input => "2020_CBG_B01"."B01001e1")',
    "table_function": "SELECT * FROM TABLE(FLATTEN(input => PARSE_JSON('[1]')))",
    "stage": "SELECT * FROM @mystage",
    "identifier": "SELECT * FROM IDENTIFIER('\"2020_CBG_B01\"')",
    "system_function": 'SELECT SYSTEM$WAIT(10) FROM "2020_CBG_B01"',
    "time_travel": 'SELECT * FROM "2020_CBG_B01" AT(OFFSET => -60)',
    "sample": 'SELECT * FROM "2020_CBG_B01" SAMPLE (10)',
    "values": "SELECT * FROM VALUES (1), (2)",
    "session_variable": 'SELECT $1 FROM "2020_CBG_B01"',
    "other_database": 'SELECT * FROM "OTHER"."PUBLIC"."2020_CBG_B01"',
    "other_schema": 'SELECT * FROM "CENSUS"."PRIVATE"."2020_CBG_B01"',
    "wrong_case_table": 'SELECT * FROM "2020_cbg_b01"',
    "unknown_qualifier": 'SELECT "zz"."B01001e1" FROM "2020_CBG_B01"',
    "repeat_in_subquery": 'SELECT "2020_CBG_B01"."CENSUS_BLOCK_GROUP" FROM "2020_CBG_B01" WHERE '
    '"2020_CBG_B01"."B01001e1" > (SELECT AVG("2020_CBG_B01"."B01001e1") FROM "2020_CBG_B01")',
    # joins
    "comma_join": 'SELECT 1 FROM "2020_CBG_B01", "2020_CBG_B19" WHERE '
    '"2020_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B19"."CENSUS_BLOCK_GROUP"',
    "cross_join": 'SELECT 1 FROM "2020_CBG_B01" CROSS JOIN "2020_CBG_B19"',
    "using_join": 'SELECT 1 FROM "2020_CBG_B01" JOIN "2020_CBG_B19" USING ("CENSUS_BLOCK_GROUP")',
    "natural_join": 'SELECT 1 FROM "2020_CBG_B01" NATURAL JOIN "2020_CBG_B19"',
    "right_join": 'SELECT 1 FROM "2020_CBG_B01" RIGHT JOIN "2020_CBG_B19" ON '
    '"2020_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B19"."CENSUS_BLOCK_GROUP"',
    "full_join": 'SELECT 1 FROM "2020_CBG_B01" FULL JOIN "2020_CBG_B19" ON '
    '"2020_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B19"."CENSUS_BLOCK_GROUP"',
    "join_extra_predicate": 'SELECT 1 FROM "2020_CBG_B01" JOIN "2020_CBG_B19" ON '
    '"2020_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B19"."CENSUS_BLOCK_GROUP" AND 1 = 1',
    "fips_wrong_columns": f'SELECT 1 FROM "2020_CBG_B23" JOIN "{FIPS}" ON '
    f'LEFT("2020_CBG_B23"."CENSUS_BLOCK_GROUP", 5) = "{FIPS}"."STATE" || "{FIPS}"."COUNTY_FIPS"',
    "fips_wrong_length": f'SELECT 1 FROM "2020_CBG_B23" JOIN "{FIPS}" ON '
    f'LEFT("2020_CBG_B23"."CENSUS_BLOCK_GROUP", 2) = "{FIPS}"."STATE_FIPS" || "{FIPS}"."COUNTY_FIPS"',
    "fips_other_year": 'SELECT 1 FROM "2020_CBG_B23" JOIN "2019_METADATA_CBG_FIPS_CODES" ON '
    'LEFT("2020_CBG_B23"."CENSUS_BLOCK_GROUP", 5) = "2019_METADATA_CBG_FIPS_CODES"."STATE_FIPS" '
    '|| "2019_METADATA_CBG_FIPS_CODES"."COUNTY_FIPS"',
    "join_to_cte": 'WITH x AS (SELECT "2020_CBG_B19"."CENSUS_BLOCK_GROUP" AS g FROM "2020_CBG_B19") '
    'SELECT 1 FROM "2020_CBG_B01" JOIN x ON "2020_CBG_B01"."CENSUS_BLOCK_GROUP" = x.g',
    # dimension aggregation through every path
    "count_dimension": 'SELECT COUNT("2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
    "max_dimension": 'SELECT MAX("2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
    "median_dimension": 'SELECT MEDIAN("2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
    "sum_dimension_arithmetic": 'SELECT SUM("2020_CBG_B19"."B19013e1" * 1) FROM "2020_CBG_B19"',
    "percentile_dimension": "SELECT PERCENTILE_CONT(0.5) WITHIN GROUP "
    '(ORDER BY "2020_CBG_B19"."B19013e1") FROM "2020_CBG_B19"',
    "lag_dimension": 'SELECT LAG("2020_CBG_B19"."B19013e1") OVER (ORDER BY '
    '"2020_CBG_B19"."CENSUS_BLOCK_GROUP") FROM "2020_CBG_B19"',
    "derived_median": 'SELECT AVG(t.v) FROM (SELECT "2020_CBG_B19"."B19013e1" AS v '
    'FROM "2020_CBG_B19") t',
    "nested_cte_median": 'WITH a AS (SELECT "2020_CBG_B19"."B19013e1" AS v FROM "2020_CBG_B19"), '
    "b AS (SELECT v * 2 AS w FROM a) SELECT SUM(w) FROM b",
    "cte_passthrough_median": 'WITH a AS (SELECT "2020_CBG_B19"."B19013e1" FROM "2020_CBG_B19") '
    'SELECT AVG(a."B19013e1") FROM a',
    "lateral_alias_median": 'SELECT "2020_CBG_B19"."B19013e1" AS m, AVG(m) FROM "2020_CBG_B19"',
    "unqualified_median": 'SELECT AVG("B19013e1") FROM "2020_CBG_B19"',
    "aliased_median": 'SELECT AVG(i."B19013e1") FROM "2020_CBG_B19" i',
    "scalar_subquery_in_aggregate": 'SELECT SUM((SELECT MAX("2020_CBG_B01"."B01001e1") '
    'FROM "2020_CBG_B01")) FROM "2020_CBG_B19"',
    "count_county": f'SELECT COUNT(DISTINCT "{FIPS}"."COUNTY") FROM "{FIPS}"',
    # untraceable / ambiguous
    "ambiguous_unqualified": 'SELECT "CENSUS_BLOCK_GROUP" FROM "2020_CBG_B01" JOIN "2020_CBG_B19" '
    'ON "2020_CBG_B01"."CENSUS_BLOCK_GROUP" = "2020_CBG_B19"."CENSUS_BLOCK_GROUP"',
    "star_cte_then_sum": 'WITH x AS (SELECT * FROM "2020_CBG_B19") SELECT SUM("B19001e1") FROM x',
    "unknown_unqualified": 'SELECT "B19001e9" FROM "2020_CBG_B19"',
    "cte_shadows_table": 'WITH "2020_CBG_B19" AS (SELECT "2020_CBG_B01"."B01001e1" AS v '
    'FROM "2020_CBG_B01") SELECT v FROM "2020_CBG_B19"',
}


def _run(sql, max_rows=1000):
    return validate_and_fix(sql, META, ALLOWED, max_rows, DB, SCHEMA)


@pytest.mark.parametrize("name", REJECT)
def test_rejected(name):
    with pytest.raises(QueryRejected):
        _run(REJECT[name])


def _reason(sql):
    with pytest.raises(QueryRejected) as e:
        _run(sql)
    return e.value.reason.lower()


def test_reject_reasons_are_specific():
    assert "2019" in _reason(REJECT["mix_years"]) and "2020" in _reason(REJECT["mix_years"])
    assert "census count" in _reason(REJECT["mix_sources"])
    assert "join" in _reason(REJECT["bad_join"])
    assert "dimension" in _reason(REJECT["avg_median"])
    assert "dimension" in _reason(REJECT["cte_median"])
    assert "more than once" in _reason(REJECT["self_join"])
    assert "qualified" in _reason(REJECT["untraceable"])
    assert "not allowed" in _reason(REJECT["excluded"])
    assert "one" in _reason(REJECT["multi"])


def test_fixes_quoting_prefix_alias_and_limit():
    out = _run('select sum(t."B01001e1") from "2020_CBG_B01" t')
    assert f'"{DB}"."{SCHEMA}"."2020_CBG_B01"' in out and " t" not in out and "LIMIT 1000" in out
    assert 'SUM("2020_CBG_B01"."B01001e1")' in out


def test_fixed_output_is_stable_when_revalidated():
    for sql in (
        'select sum(t."B01001e1") from "2020_CBG_B01" t',
        RANK_TOP10_UNEMPLOYMENT_SQL,
        'SELECT "B01001e1" FROM "2020_CBG_B01"',
    ):
        out = _run(sql)
        assert _run(out) == out


def test_full_prefix_accepted():
    out = _run(f'SELECT "{FIPS}"."COUNTY" FROM {DB}.{SCHEMA}."{FIPS}"')
    assert f'FROM "{DB}"."{SCHEMA}"."{FIPS}"' in out
    assert f'"{FIPS}"."COUNTY"' in out


def test_unqualified_column_is_qualified_when_unambiguous():
    out = _run('SELECT "B01001e1" FROM "2020_CBG_B01"')
    assert 'SELECT "2020_CBG_B01"."B01001e1"' in out


def test_fips_join_allowed():
    _run(
        'SELECT "2020_METADATA_CBG_FIPS_CODES"."COUNTY", SUM("2020_CBG_B01"."B01003e1") FROM "2020_CBG_B01" '
        'JOIN "2020_METADATA_CBG_FIPS_CODES" ON LEFT("2020_CBG_B01"."CENSUS_BLOCK_GROUP", 5) = '
        '"2020_METADATA_CBG_FIPS_CODES"."STATE_FIPS" || "2020_METADATA_CBG_FIPS_CODES"."COUNTY_FIPS" GROUP BY 1'
    )


def test_block_group_join_and_left_join_allowed():
    _run(
        'SELECT SUM("2020_CBG_B01"."B01003e1"), SUM("2020_CBG_B19"."B19001e1") FROM "2020_CBG_B01" '
        'LEFT JOIN "2020_CBG_B19" ON "2020_CBG_B19"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"'
    )
    _run(
        f'SELECT "{FIPS}"."STATE", SUM("2020_CBG_B23"."B23025e1") FROM "2020_CBG_B23" '
        f'INNER JOIN "{FIPS}" ON {FIPS_ON} GROUP BY "{FIPS}"."STATE"'
    )


def test_census_count_with_fips_allowed():
    _run(
        f'SELECT "{FIPS}"."COUNTY", SUM("2020_REDISTRICTING_CBG_DATA"."P0010001") '
        f'FROM "2020_REDISTRICTING_CBG_DATA" JOIN "{FIPS}" ON '
        f'LEFT("2020_REDISTRICTING_CBG_DATA"."CENSUS_BLOCK_GROUP", 5) = '
        f'"{FIPS}"."STATE_FIPS" || "{FIPS}"."COUNTY_FIPS" GROUP BY 1'
    )


def test_ranking_cte_allowed():  # Review Focus 3
    out = _run(RANK_TOP10_UNEMPLOYMENT_SQL)
    assert "RANK() OVER" in out and "LIMIT 1000" in out
    assert f'"{DB}"."{SCHEMA}"."2020_CBG_B23"' in out


def test_dimension_allowed_outside_aggregates():
    _run(
        'SELECT "2020_CBG_B19"."B19013e1" FROM "2020_CBG_B19" WHERE "2020_CBG_B19"."B19013e1" > 50000'
    )
    _run(
        'SELECT "2020_CBG_B19"."CENSUS_BLOCK_GROUP", RANK() OVER '
        '(ORDER BY "2020_CBG_B19"."B19013e1" DESC) FROM "2020_CBG_B19"'
    )
    _run(f'SELECT "{FIPS}"."COUNTY", COUNT(*) FROM "{FIPS}" GROUP BY "{FIPS}"."COUNTY"')


def test_block_group_key_may_be_counted():
    _run('SELECT COUNT(DISTINCT "2020_CBG_B01"."CENSUS_BLOCK_GROUP"), COUNT(*) FROM "2020_CBG_B01"')


def test_measure_aggregates_through_cte_and_subquery_allowed():
    _run(
        'WITH x AS (SELECT "2020_CBG_B01"."B01001e1" AS v FROM "2020_CBG_B01") '
        "SELECT SUM(v) AS total FROM x ORDER BY total"
    )
    _run('SELECT AVG(t.v) FROM (SELECT SUM("2020_CBG_B19"."B19313e1") AS v FROM "2020_CBG_B19") t')
    _run(
        'SELECT "2020_CBG_B01"."CENSUS_BLOCK_GROUP" FROM "2020_CBG_B01" WHERE '
        '"2020_CBG_B01"."B01001e1" > (SELECT AVG("2020_CBG_B19"."B19001e1") FROM "2020_CBG_B19")'
    )


def test_limit_added_lowered_or_kept():
    assert _run('SELECT "2020_CBG_B01"."B01001e1" FROM "2020_CBG_B01"', 50).endswith("LIMIT 50")
    assert _run('SELECT "2020_CBG_B01"."B01001e1" FROM "2020_CBG_B01" LIMIT 5000', 50).endswith(
        "LIMIT 50"
    )
    assert _run('SELECT "2020_CBG_B01"."B01001e1" FROM "2020_CBG_B01" LIMIT 7', 50).endswith(
        "LIMIT 7"
    )


def test_unknown_column_suggests_close_match():
    with pytest.raises(QueryRejected) as e:
        _run(UNKNOWN_COL_TYPO_SQL)
    assert "B01001e1" in e.value.suggestions
    assert len(e.value.suggestions) <= 3


def test_unknown_table_suggests_close_match():
    with pytest.raises(QueryRejected) as e:
        _run('SELECT * FROM "2020_CBG_B0"')
    assert "2020_CBG_B01" in e.value.suggestions
