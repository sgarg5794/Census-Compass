import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from census_compass.semantic.members import (
    classification_report,
    classify_group,
    convert_group,
    describe,
    key_dimension,
)
from census_compass.semantic.sources import load_census_api_labels, load_topics

TABLES = Path("data/tables")
API = Path("definitions/source/census_api")
CODE = re.compile(r"\b[BCP]\d{3,5}[A-I]?(?:[em]\d+)?\b")


def _table(name):
    return yaml.safe_load((TABLES / f"{name}.yaml").read_text())


@pytest.fixture(scope="module")
def v1_b01_2020():
    return _table("2020_CBG_B01")


@pytest.fixture(scope="module")
def v1_b19_2020():
    return _table("2020_CBG_B19")


@pytest.fixture(scope="module")
def v1_b25_2020():
    return _table("2020_CBG_B25")


@pytest.fixture(scope="module")
def labels_2020():
    return load_census_api_labels(API, 2020)


def test_median_is_dimension_and_count_is_sum(v1_b01_2020):
    dims, measures, _ = convert_group(v1_b01_2020, "B01002", 2020, {})
    assert not measures and dims
    assert all(d.type == "number" for d in dims if d.name != "census_block_group")
    _, m2, _ = convert_group(v1_b01_2020, "B01001", 2020, {})
    assert {m.type for m in m2} == {"sum"}
    assert m2[0].sql == '"2020_CBG_B01"."B01001e1"'
    assert m2[0].meta["moe_sql"] == '"2020_CBG_B01"."B01001m1"'
    assert m2[0].name == "population"


def test_aggregate_income_is_sum():
    assert classify_group("Aggregate Household Income In The Past 12 Months") == "sum"
    assert classify_group("Per Capita Income In The Past 12 Months") == "dimension"
    assert classify_group("Median Gross Rent (Dollars)") == "dimension"
    assert classify_group("Gini Index Of Income Inequality") == "dimension"
    assert classify_group("Tenure") == "sum"
    # a distribution of people by ratio bracket is a count, not a published ratio
    assert classify_group("Ratio Of Income To Poverty Level In The Past 12 Months") == "sum"


def test_published_per_capita_renamed(v1_b19_2020):
    dims, _, meta = convert_group(v1_b19_2020, "B19301", 2020, {})
    assert dims[0].name.startswith("block_group_")
    assert all(d.name.startswith("block_group_") for d in dims)
    assert dims[0].format == "currency" and meta[0].aggregation == "none"
    assert {d.meta.get("population_group") for d in dims} == {None, *"ABCDEFGHI"}


def test_average_household_size_renamed(v1_b25_2020):
    dims, _, _ = convert_group(v1_b25_2020, "B25010", 2020, {})
    assert dims and all(d.name.startswith("block_group_") for d in dims)


def test_e_columns_use_api_labels_and_race_tag(v1_b25_2020):
    _, ms, meta = convert_group(v1_b25_2020, "B25003E", 2020,
                                {"B25003Ee2": "Total:!!Owner occupied"})
    assert "Owner occupied" in ms[1].description and "Pacific Islander" in ms[1].description
    assert ms[1].meta["population_group"] == "E" and meta[1].definition_source == "census_api"
    assert ms[1].sql == '"2020_CBG_B25"."B25003Ee2"'
    assert ms[1].meta["moe_sql"] == '"2020_CBG_B25"."B25003Em2"'
    assert ms[1].name == ("tenure_native_hawaiian_and_other_pacific_islander_alone_householder"
                          "_owner_occupied_b25003ee2")
    assert ms[1].meta["universe"] == (
        "Occupied housing units with a householder who is "
        "Native Hawaiian and Other Pacific Islander alone")


def test_e_names_follow_v1_pattern(v1_b01_2020, labels_2020):
    dims, _, _ = convert_group(v1_b01_2020, "B01002", 2020, labels_2020)
    names = {d.meta.get("population_group"): d.name for d in dims if d.sql.endswith('e2"')}
    assert names["A"] == "median_age_by_sex_white_alone_male"
    assert names["E"] == "median_age_by_sex_native_hawaiian_and_other_pacific_islander_alone_male"


def test_base_group_includes_race_versions_in_order(v1_b25_2020, labels_2020):
    _, ms, meta = convert_group(v1_b25_2020, "B25003", 2020, labels_2020)
    groups = [m.meta.get("population_group") for m in ms]
    assert groups == [None] * 3 + [g for g in "ABCDEFGHI" for _ in range(3)]
    assert [x.member for x in meta] == [m.name for m in ms]
    base = meta[0]
    assert (base.file, base.kind, base.table, base.source_column, base.aggregation,
            base.definition_source, base.population_group) == (
        "", "measure", "2020_CBG_B25", "B25003e1", "sum", "dictionary", None)
    e_meta = [x for x in meta if x.population_group == "E"]
    assert {x.definition_source for x in e_meta} == {"census_api"}


def test_no_completeness_fields_in_llm_members(v1_b25_2020):
    _, ms, _ = convert_group(v1_b25_2020, "B25003", 2020, {})
    assert all(not ({"coverage_sql", "missing_sql"} & m.meta.keys()) for m in ms)


def test_margins_are_never_members(v1_b25_2020, labels_2020):
    dims, ms, _ = convert_group(v1_b25_2020, "B25003", 2020, labels_2020)
    assert not any(re.search(r'm\d+"$', m.sql) for m in [*dims, *ms])


def test_describe_template():
    text = describe("Tenure", "Occupied housing units", ["Owner occupied"], None)
    assert text == "Tenure. Universe: Occupied housing units. Owner occupied."
    race = describe("Tenure (Two Or More Races Householder)", "Occupied housing units",
                    ["Owner occupied"], "G")
    assert race.endswith("Owner occupied (Two or more races; also called multiracial, mixed race).")
    assert describe("T", "U", ["A", "B"], "F") == "T. Universe: U. A > B (Some other race alone)."


def test_formats(v1_b01_2020, v1_b25_2020, v1_b19_2020):
    dims, _, _ = convert_group(v1_b01_2020, "B01002", 2020, {})
    assert {d.format for d in dims} == {"years"}
    assert {d.format for d in convert_group(v1_b25_2020, "B25064", 2020, {})[0]} == {"currency"}
    assert {d.format for d in convert_group(v1_b25_2020, "B25071", 2020, {})[0]} == {"percent"}
    assert {d.format for d in convert_group(v1_b25_2020, "B25018", 2020, {})[0]} == {"number"}
    assert {m.format for m in convert_group(v1_b19_2020, "B19025", 2020, {})[1]} == {"currency"}
    assert {m.format for m in convert_group(v1_b19_2020, "B19001", 2020, {})[1]} == {"number"}


def test_redistricting_group():
    table = _table("2020_REDISTRICTING_CBG_DATA")
    dims, ms, meta = convert_group(table, "P003", 2020, {})
    assert not dims and len(ms) == 71
    assert ms[0].sql == '"2020_REDISTRICTING_CBG_DATA"."P0030001"' and "moe_sql" not in ms[0].meta
    assert ms[0].meta["universe"] == "Total population 18 years and over"
    assert {x.definition_source for x in meta} == {"dictionary"}


def test_key_dimension(v1_b25_2020):
    dim, meta = key_dimension(v1_b25_2020, 2020)
    assert dim.name == "census_block_group" and dim.primary_key and dim.type == "string"
    assert dim.sql == '"2020_CBG_B25"."CENSUS_BLOCK_GROUP"'
    assert meta.definition_source == "project" and meta.kind == "dimension"


def test_wrong_year_or_unknown_group_rejected(v1_b25_2020):
    with pytest.raises(ValueError):
        convert_group(v1_b25_2020, "B25003", 2019, {})
    with pytest.raises(ValueError):
        convert_group(v1_b25_2020, "B99999", 2020, {})


@pytest.mark.parametrize("name", ["2020_CBG_B25", "2020_CBG_B19"])
def test_all_groups_of_real_tables_have_unique_names(name, labels_2020):
    table = _table(name)
    suffix = name.removeprefix("2020_")
    groups = [g for t in load_topics(Path("definitions/source/topics.yaml"))
              if t.table == suffix for g in t.groups]
    names, columns = [], []
    for group in groups:
        dims, ms, meta = convert_group(table, group, 2020, labels_2020)
        names += [m.name for m in [*dims, *ms]]
        columns += [x.source_column for x in meta]
        assert not any(CODE.search(m.description) for m in [*dims, *ms])
    assert [n for n, c in Counter(names).items() if c > 1] == []
    assert len(set(columns)) == len(columns)
    estimates = {f["metadata"]["source_column"] for f in table["measures"].values()}
    estimates |= {c for c in table["unresolved_columns"] if re.search(r"e\d+$", c)}
    assert set(columns) == estimates


def test_classification_report_covers_every_2020_group():
    tables = [_table(p.stem) for p in sorted(TABLES.glob("2020_*.yaml"))
              if re.fullmatch(r"2020_(CBG_[BC]\d+|REDISTRICTING_CBG_DATA)", p.stem)]
    report = classification_report(tables)
    groups = Counter(group for group, _, _ in report)
    assert groups and max(groups.values()) == 1
    assert {c for _, _, c in report} == {"sum", "dimension"}
    expected = set()
    for table in tables:
        for field in table["measures"].values():
            meta = field["metadata"]["source_metadata"]
            expected.add(meta.get("TABLE_NUMBER") or meta["COLUMN_ID"][:4])
        expected |= {c[:7] for c in table["unresolved_columns"]}
    assert set(groups) == expected
    assert ("C17002", "Ratio Of Income To Poverty Level In The Past 12 Months", "sum") in report


def test_puerto_rico_group_codes():
    table = _table("2020_CBG_B07")
    _, ms, _ = convert_group(table, "B07201PR", 2020, {})
    assert ms and all('"2020_CBG_B07"."B07201PRe' in m.sql for m in ms)
    assert not any("population_group" in m.meta for m in ms)


def _member(table, group, column, labels=None):
    dims, ms, _ = convert_group(table, group, 2020, labels or {})
    return next(m for m in [*dims, *ms] if m.sql.endswith(f'."{column}"'))


def test_breakdown_keeps_meaningful_segments():
    b03 = _table("2020_CBG_B03")
    total, hispanic = _member(b03, "B03003", "B03003e1"), _member(b03, "B03003", "B03003e3")
    assert "Hispanic or Latino" in hispanic.title and "Hispanic or Latino" in hispanic.description
    assert (hispanic.title, hispanic.description) != (total.title, total.description)
    hispanic_white = _member(b03, "B03002", "B03002e13")
    for text in (hispanic_white.title, hispanic_white.description):
        assert "Hispanic or Latino" in text and "White alone" in text
    alone = _member(_table("2020_CBG_B09"), "B09019", "B09019e5")
    assert "Living alone" in alone.title and "Living alone" in alone.description


def test_breakdown_drops_only_restatements(v1_b19_2020):
    member = _member(v1_b19_2020, "B19301", "B19301e1")
    assert member.title.endswith(": Total") and member.description.endswith(". Total.")
    race = _member(v1_b19_2020, "B19301", "B19301Ae1")
    assert race.title.endswith(": Total")


@pytest.mark.parametrize("year", [2019, 2020])
def test_titles_and_descriptions_unique_within_every_topic(year):
    labels = load_census_api_labels(API, year)
    problems = []
    for topic in load_topics(Path("definitions/source/topics.yaml")):
        path = TABLES / f"{year}_{topic.table}.yaml"
        if topic.internal or not path.exists():
            continue
        table = yaml.safe_load(path.read_text())
        members = []
        for group in topic.groups:
            if group == "B28012" and year == 2019:  # absent in 2019 (topics.yaml)
                continue
            dims, ms, _ = convert_group(table, group, year, labels)
            members += [*dims, *ms]
        for field in ("title", "description"):
            counts = Counter(getattr(m, field) for m in members)
            problems += [(topic.name, field, v) for v, c in counts.items() if c > 1]
    assert problems == []


def test_shared_group_titles_are_told_apart_by_universe():
    b28 = _table("2020_CBG_B28")
    households, people = _member(b28, "B28003", "B28003e2"), _member(b28, "B28008", "B28008e2")
    assert households.title != people.title
    assert "(Households)" in households.title and "(Population in Households)" in people.title
    unique = _member(b28, "B28002", "B28002e1")
    assert "(Households)" not in unique.title
