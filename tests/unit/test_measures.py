import re
import sqlite3
from collections import Counter
from functools import cache
from pathlib import Path

import pytest
import sqlglot
import yaml

from census_compass.semantic.measures import (
    column_tables,
    expand,
    load_templates,
    render,
)
from census_compass.semantic.members import convert_group
from census_compass.semantic.race_groups import RACE_GROUPS
from census_compass.semantic.sources import load_census_api_labels, load_topics

MEASURES = Path("definitions/source/measures.yaml")
TOPICS = Path("definitions/source/topics.yaml")
TABLES = Path("data/tables")
API = Path("definitions/source/census_api")
CASES = yaml.safe_load(Path("tests/fixtures/measures/cases.yaml").read_text())
CODE = re.compile(r"\b[BCP]\d{3,5}[A-I]?(?:[em]\d+)?\b")
SPARSE = ("The Census Bureau does not publish this for every block group; block groups without "
          "it are left out.")

TEMPLATES = load_templates(MEASURES)
T = {t.name: t for t in TEMPLATES}
EXPANDED = {t.name: t for t in expand(TEMPLATES, RACE_GROUPS)}


@cache
def _tables(year):
    return column_tables(year, TABLES)


def _inputs(template):
    columns = [c.lstrip("-") for c in [*template.numerator, *template.denominator]]
    return list(dict.fromkeys(columns))


def evaluate(template, rows, year=2020, coverage=False):
    """Run the rendered measure (or coverage) SQL on block-group rows in SQLite.

    Rows are tuples in input order (numerator then denominator columns) or dicts by column.
    """
    measure, joins, meta = render(template, year, _tables(year))
    inputs = _inputs(template)
    tables = _tables(year)
    by_table = {meta.table: []}
    for column in inputs:
        by_table.setdefault(tables[column], []).append(column)
    db = sqlite3.connect(":memory:")
    for table, columns in by_table.items():
        defs = ", ".join(['"CENSUS_BLOCK_GROUP" TEXT', *(f'"{c}" REAL' for c in columns)])
        db.execute(f'CREATE TABLE "{table}" ({defs})')
    for number, row in enumerate(rows):
        values = row if isinstance(row, dict) else dict(zip(inputs, row, strict=True))
        for table, columns in by_table.items():
            marks = ", ".join("?" * (len(columns) + 1))
            names = ", ".join(f'"{c}"' for c in ["CENSUS_BLOCK_GROUP", *columns])
            db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})',
                       [str(number), *(values[c] for c in columns)])
    expression = meta.coverage_sql if coverage else measure.sql
    joined = "".join(f' JOIN "{_joined_table(j.sql)}" ON {j.sql}' for j in joins)
    query = f'SELECT {expression} FROM "{meta.table}"{joined}'
    return db.execute(sqlglot.transpile(query, read="snowflake", write="sqlite")[0]).fetchone()[0]


def _joined_table(join_sql):
    return re.findall(r'"([^"]+)"\."CENSUS_BLOCK_GROUP"', join_sql)[1]


def _approx(value):
    return None if value is None else pytest.approx(value)


# --- rendering ---------------------------------------------------------------------------

def test_render_unemployment_sql_and_coverage():
    m, joins, meta = render(T["unemployment_rate"], 2020,
                            {"B23025e5": "2020_CBG_B23", "B23025e3": "2020_CBG_B23"})
    assert joins == [] and m.type == "number" and m.format == "percent"
    assert meta.numerator_columns == ["2020_CBG_B23.B23025e5"] and meta.coverage_sql
    assert meta.denominator_columns == ["2020_CBG_B23.B23025e3"]
    pred = '"2020_CBG_B23"."B23025e5" IS NULL OR "2020_CBG_B23"."B23025e3" IS NULL'
    assert m.sql == (f'100 * SUM(IFF({pred}, NULL, "2020_CBG_B23"."B23025e5")) / '
                     f'NULLIF(SUM(IFF({pred}, NULL, "2020_CBG_B23"."B23025e3")), 0)')
    assert meta.coverage_sql == (f'SUM(IFF({pred}, NULL, "2020_CBG_B23"."B23025e3")) / '
                                 'NULLIF(SUM("2020_CBG_B23"."B23025e3"), 0)')
    assert m.meta == {"universe": "Civilian labor force"}
    assert (meta.kind, meta.aggregation, meta.definition_source) == (
        "measure", "formula", "hand_written")
    assert meta.member == "unemployment_rate" and meta.table == "2020_CBG_B23"
    assert meta.subject_definitions_page == "68" and meta.source_column is None
    assert meta.normalized_sql == sqlglot.parse_one(m.sql, read="snowflake").sql(
        dialect="snowflake")


def test_scale_one_has_no_multiplication():
    m, _, _ = render(T["mean_household_income"], 2020, _tables(2020))
    assert m.sql.startswith("SUM(IFF(") and m.format == "currency"


def test_cross_table_join_generated():
    m, joins, meta = render(T["per_capita_income"], 2020,
                            {"B19313e1": "2020_CBG_B19", "B01003e1": "2020_CBG_B01"})
    assert len(joins) == 1 and joins[0].relationship == "one_to_one"
    assert joins[0].sql == '"2020_CBG_B19"."CENSUS_BLOCK_GROUP" = "2020_CBG_B01"."CENSUS_BLOCK_GROUP"'
    assert joins[0].name == "population_age_and_sex_2020"
    assert meta.denominator_columns == ["2020_CBG_B01.B01003e1"]
    assert '"2020_CBG_B01"."B01003e1"' in m.sql


def test_subtracted_denominator_term():
    m, _, meta = render(T["renters_paying_30_percent_or_more"], 2019, _tables(2019))
    den = '"2019_CBG_B25"."B25070e1" - "2019_CBG_B25"."B25070e11"'
    assert f"NULL, {den})), 0)" in m.sql
    assert meta.coverage_sql.endswith(f"NULLIF(SUM({den}), 0)")
    assert meta.denominator_columns == ["2019_CBG_B25.B25070e1", "2019_CBG_B25.B25070e11"]


def test_predicate_checks_every_input_once_in_order():
    m, _, _ = render(T["homeowner_vacancy_rate"], 2020, _tables(2020))
    pred = ('"2020_CBG_B25"."B25004e4" IS NULL OR "2020_CBG_B25"."B25003e2" IS NULL '
            'OR "2020_CBG_B25"."B25004e5" IS NULL')
    assert m.sql.count(f"IFF({pred}, NULL, ") == 2


def test_unknown_column_rejected():
    with pytest.raises(ValueError, match="B23025e5"):
        render(T["unemployment_rate"], 2020, {"B23025e3": "2020_CBG_B23"})


def test_unexpanded_template_rejected():
    with pytest.raises(ValueError, match="expand"):
        render(T["mean_household_income_{slug}"], 2020, _tables(2020))


# --- race expansion ----------------------------------------------------------------------

def test_race_template_expands_to_nine_tagged_measures():
    expanded = expand([T["mean_household_income_{slug}"]], RACE_GROUPS)
    names = [t.name for t in expanded]
    assert len(names) == 9 and "mean_household_income_asian_alone" in names
    assert [t.population_group for t in expanded] == list("ABCDEFGHI")
    asian = EXPANDED["mean_household_income_asian_alone"]
    assert asian.numerator == ["B19025De1"] and asian.denominator == ["B25003De1"]
    assert asian.for_each is None and "{" not in asian.title + asian.description


def test_race_measure_rendered_with_tag_join_and_sparse_note():
    m, joins, meta = render(EXPANDED["mean_household_income_asian_alone"], 2020, _tables(2020))
    assert m.meta["population_group"] == "D" and meta.population_group == "D"
    assert "Asian alone" in m.title
    assert "Asian alone" in m.description and "also called Asian" in m.description
    assert m.description.endswith(SPARSE)
    assert [j.name for j in joins] == ["housing_occupancy_and_tenure_2020"]
    assert joins[0].sql == ('"2020_CBG_B19"."CENSUS_BLOCK_GROUP" = '
                            '"2020_CBG_B25"."CENSUS_BLOCK_GROUP"')
    hispanic, _, _ = render(EXPANDED["mean_household_income_hispanic_or_latino"], 2020,
                            _tables(2020))
    assert "Latinx" in hispanic.description


def test_non_sparse_measure_has_no_sparse_note():
    m, _, _ = render(T["unemployment_rate"], 2020, _tables(2020))
    assert SPARSE not in m.description and "population_group" not in m.meta


# --- SQL on fixtures (SQLite) -------------------------------------------------------------

@pytest.mark.parametrize("rows,expected_value,expected_coverage", [
    ([(10, 100), (20, 100)], 15.0, 1.0),          # complete
    ([(None, 100), (20, 100)], 20.0, 0.5),        # missing numerator
    ([(10, None), (20, 100)], 20.0, 1.0),         # missing denominator (unknown denominator)
    ([(0, 0)], None, None),                        # zero denominator -> not available
    ([(None, None)], None, None),                  # no valid rows
])
def test_validity_predicate_on_fixture(rows, expected_value, expected_coverage):
    template = T["unemployment_rate"]
    assert evaluate(template, rows) == _approx(expected_value)
    assert evaluate(template, rows, coverage=True) == _approx(expected_coverage)


def test_block_group_without_published_income_is_not_zero():
    # 238-row case: aggregate income NULL, population > 0 -> per_capita_income NULL
    assert evaluate(T["per_capita_income"], [(None, 25)]) is None


@pytest.mark.parametrize("case", CASES, ids=[c["case"] for c in CASES])
def test_fixture_cases(case):
    template = EXPANDED[case["measure"]]
    assert evaluate(template, case["rows"]) == _approx(case["value"])
    assert evaluate(template, case["rows"], coverage=True) == _approx(case["coverage"])


# --- the authored catalogue --------------------------------------------------------------

def test_catalogue_size_and_unique_names():
    assert len(TEMPLATES) == 31 and len(EXPANDED) == 39
    assert len(set(EXPANDED)) == len(EXPANDED)


@pytest.mark.parametrize("year", [2019, 2020])
def test_every_measure_renders_and_validates(year):
    topics = {t.name: t for t in load_topics(TOPICS)}
    joined = set()
    for template in EXPANDED.values():
        m, joins, meta = render(template, year, _tables(year))
        assert template.topic in topics
        assert meta.table == f"{year}_{topics[template.topic].table}"
        assert meta.coverage_sql and meta.numerator_columns and meta.denominator_columns
        assert meta.subject_definitions_page and "page" not in m.meta
        assert not CODE.search(m.description) and not CODE.search(m.title)
        assert "IFF(" not in m.description and "coverage" not in str(m.meta)
        sqlglot.parse_one(m.sql, read="snowflake")
        sqlglot.parse_one(meta.coverage_sql, read="snowflake")
        if joins:
            joined.add(template.name)
    race = {f"mean_household_income_{g.slug}" for g in RACE_GROUPS}
    assert joined == {"per_capita_income", *race}


@pytest.mark.parametrize("year", [2019, 2020])
def test_measure_names_do_not_collide_with_topic_members(year):
    labels = load_census_api_labels(API, year)
    topics = {t.name: t for t in load_topics(TOPICS)}
    loaded = {}
    for topic in {t.topic for t in EXPANDED.values()}:
        spec = topics[topic]
        name = f"{year}_{spec.table}"
        table = loaded.setdefault(name, yaml.load((TABLES / f"{name}.yaml").read_text(),
                                                  Loader=yaml.CSafeLoader))
        members = []
        for group in spec.groups:
            if group == "B28012" and year == 2019:  # absent in 2019 (topics.yaml)
                continue
            dims, ms, _ = convert_group(table, group, year, labels)
            members += [x.name for x in [*dims, *ms]]
        mine = [t.name for t in EXPANDED.values() if t.topic == topic]
        assert not set(mine) & set(members), topic
        assert not [n for n, c in Counter(mine).items() if c > 1]


@pytest.mark.parametrize("year", [2019, 2020])
def test_column_tables_resolves_documented_and_e_columns(year):
    tables = _tables(year)
    assert tables["B23025e5"] == f"{year}_CBG_B23"
    assert tables["B25003Ee1"] == f"{year}_CBG_B25"
    assert tables["C16002e4"] == f"{year}_CBG_C16"
    assert not any(re.search(r"m\d+$", c) for c in tables)
