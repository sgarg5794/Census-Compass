import json
import re
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest
import yaml

from census_compass.config import Settings
from census_compass.contracts import (
    MemberMetadata,
    load_allowed_tables,
    load_builder_metadata,
    load_topic_files,
)
from census_compass.semantic.build import (
    allowed_tables,
    generate,
    load_v1_tables,
    run_checks,
    size_warnings,
    write_outputs,
)
from census_compass.semantic.sources import load_topics

TOPICS = Path("definitions/source/topics.yaml")
CODE = re.compile(r"\b[BCP]\d{3,5}[A-I]?(?:PR)?\b")
INTERNAL = {"geography", "land_area_and_location"}


@pytest.fixture(scope="module")
def settings():
    return Settings()


@cache
def _built(year):
    return generate(Settings(), year)


@cache
def _v1(year):
    return load_v1_tables(Settings(), year)


def _topics():
    return load_topics(TOPICS)


def _copy(year):
    files, meta = _built(year)
    return [f.model_copy(deep=True) for f in files], meta.model_copy(deep=True)


def _file(files, name):
    return next(f for f in files if f.name == name)


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_build_passes_all_checks(settings, year):
    files, meta = _built(year)
    assert run_checks(files, meta, _topics(), _v1(year), settings) == []


def test_file_counts_and_names():
    files19, _ = _built(2019)
    files20, _ = _built(2020)
    names20 = {f.name for f in files20}
    assert len(files19) == 44 + 2 and len(files20) == 47 + 2
    assert "census_count_race_and_hispanic_2020" in names20
    assert not any(n.endswith("_2020_2020") for n in names20)
    assert not any(n.startswith("census_count") for n in {f.name for f in files19})
    assert {"geography_2019", "land_area_and_location_2019"} <= {f.name for f in files19}


def test_oversize_topic_warns_but_does_not_fail(settings, monkeypatch):
    monkeypatch.setattr(settings, "topic_token_limit", 100)
    files, meta = _built(2020)
    assert run_checks(files, meta, _topics(), _v1(2020), settings) == []
    warnings = size_warnings(files, settings)
    assert any("rent_and_rent_burden_2020" in w and "tokens" in w for w in warnings)


def test_size_warnings_name_file_and_estimate(settings):
    files, _ = _built(2020)
    for warning in size_warnings(files, settings):
        assert re.search(r"^\S+_2020: about [\d,]+ tokens", warning)


def _topics_without(group):
    return [replace(t, groups=[g for g in t.groups if g != group]) for t in _topics()]


def test_unassigned_group_detected(settings):
    files, meta = _built(2020)
    errors = run_checks(files, meta, _topics_without("B25004"), _v1(2020), settings)
    assert any("B25004" in e and "not assigned" in e for e in errors)


def test_group_assigned_twice_detected(settings):
    files, meta = _built(2020)
    topics = [replace(t, groups=[*t.groups, "B25004"]) if t.name == "rent_and_rent_burden"
              else t for t in _topics()]
    errors = run_checks(files, meta, topics, _v1(2020), settings)
    assert any("B25004" in e and "more than one topic" in e for e in errors)


def test_group_in_neither_year_is_an_error(settings):
    topics = [replace(t, groups=[*t.groups, "B25999"]) if t.name == "rent_and_rent_burden"
              else t for t in _topics()]
    with pytest.raises(ValueError, match="B25999"):
        generate(settings, 2020, topics=topics)


def test_group_absent_in_2019_is_skipped():
    files, meta = _built(2019)
    assert not any(m.source_column and m.source_column.startswith("B28012")
                   for m in meta.members)
    _, meta20 = _built(2020)
    assert any(m.source_column and m.source_column.startswith("B28012")
               for m in meta20.members)


def test_undeclared_join_detected(settings):
    files, meta = _copy(2020)
    _file(files, "per_capita_income_2020").joins = []
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("per_capita_income_2020" in e and "2020_CBG_B01" in e for e in errors)


def test_unknown_column_detected(settings):
    files, meta = _copy(2020)
    target = _file(files, "housing_occupancy_and_tenure_2020")
    target.measures[0].sql = '"2020_CBG_B25"."B25999e1"'
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("B25999e1" in e and "does not exist" in e for e in errors)


def test_second_physical_table_detected(settings):
    files, meta = _copy(2020)
    target = _file(files, "housing_occupancy_and_tenure_2020")
    target.measures[0].sql = '"2020_CBG_B19"."B19001e1"'
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("housing_occupancy_and_tenure_2020" in e and "2020_CBG_B19" in e
               for e in errors)


def test_completeness_text_in_llm_file_detected(settings):
    files, meta = _copy(2020)
    _file(files, "poverty_2020").measures[0].meta["coverage_sql"] = "SUM(1)"
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("poverty_2020" in e and "coverage_sql" in e for e in errors)


def test_ratio_without_coverage_detected(settings):
    files, meta = _copy(2020)
    meta.members = [m.model_copy(update={"coverage_sql": None})
                    if m.member == "poverty_rate" else m for m in meta.members]
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("poverty_rate" in e and "coverage_sql" in e for e in errors)


def test_excluded_table_detected(settings):
    files, meta = _copy(2020)
    meta.table_columns["2020_CBG_B99"] = ["CENSUS_BLOCK_GROUP"]
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("2020_CBG_B99" in e for e in errors)


def test_schema_violation_detected(settings):
    files, meta = _copy(2020)
    meta.members.append(MemberMetadata.model_construct(
        file="x", member="y", kind="other", table="t", source_column=None,
        aggregation="sum", definition_source="project"))
    errors = run_checks(files, meta, _topics(), _v1(2020), settings)
    assert any("schema" in e for e in errors)


@pytest.mark.parametrize("year", [2019, 2020])
def test_all_e_columns_described(year):
    files, meta = _built(year)
    api = [m for m in meta.members if m.definition_source == "census_api"]
    assert len(api) == 74  # 74 estimates; with their 74 margins the 148 E columns
    members = {(f.name, m.name): m for f in files for m in [*f.dimensions, *f.measures]}
    assert all(members[(m.file, m.member)].description for m in api)
    assert all(members[(m.file, m.member)].meta.get("moe_sql") for m in api)


def test_period_lines():
    f19 = _file(_built(2019)[0], "household_income_2019")
    f20 = _file(_built(2020)[0], "household_income_2020")
    count = _file(_built(2020)[0], "census_count_race_and_hispanic_2020")
    assert f19.description.startswith(
        "ACS 5-year estimates, survey answers collected 2015–2019, for each Census block "
        "group: ")
    assert f20.description.startswith("ACS 5-year estimates, survey answers collected 2016–2020")
    assert count.description.startswith(
        "Official 2020 Census count as of April 1, 2020, for each Census block group: ")
    assert count.description.endswith(
        "The preferred source for 2020 population, race and Hispanic origin counts.")


@pytest.mark.parametrize("year", [2019, 2020])
def test_file_text_has_no_census_codes(year):
    for f in _built(year)[0]:
        assert not CODE.search(f.description), f.name
        assert not CODE.search(f.title), f.name


def test_topic_file_contents():
    files, meta = _built(2020)
    housing = _file(files, "housing_occupancy_and_tenure_2020")
    assert housing.sql_table == (
        '"US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET"."PUBLIC"."2020_CBG_B25"')
    assert [d.name for d in housing.dimensions].count("census_block_group") == 1
    assert housing.dimensions[0].primary_key
    names = {m.name for m in housing.measures}
    assert {"homeowner_vacancy_rate", "homeownership_rate"} <= names
    pci = _file(files, "per_capita_income_2020")
    assert [j.name for j in pci.joins] == ["population_age_and_sex_2020"]
    by_member = {(m.file, m.member): m for m in meta.members}
    assert by_member[("housing_occupancy_and_tenure_2020", "homeowner_vacancy_rate")
                     ].coverage_sql
    assert len(by_member) == len(meta.members)


def test_table_columns_cover_estimates_margins_and_keys():
    _, meta = _built(2020)
    b25 = set(meta.table_columns["2020_CBG_B25"])
    assert {"CENSUS_BLOCK_GROUP", "B25001e1", "B25001m1", "B25003Ee1", "B25003Em1"} <= b25
    assert len(b25) == 1741
    assert set(meta.table_columns["2020_METADATA_CBG_FIPS_CODES"]) >= {
        "STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY"}
    assert meta.fips_table == "2020_METADATA_CBG_FIPS_CODES"


def test_internal_files_are_dimensions_only():
    files, meta = _built(2019)
    geo = _file(files, "geography_2019")
    land = _file(files, "land_area_and_location_2019")
    assert geo.measures == [] and land.measures == []
    assert {d.sql for d in geo.dimensions} == {
        f'"2019_METADATA_CBG_FIPS_CODES"."{c}"'
        for c in ("STATE", "STATE_FIPS", "COUNTY_FIPS", "COUNTY")}
    assert {d.sql.split(".")[1].strip('"') for d in land.dimensions} == {
        "CENSUS_BLOCK_GROUP", "AMOUNT_LAND", "AMOUNT_WATER", "LATITUDE", "LONGITUDE"}
    assert all(d.description for d in [*geo.dimensions, *land.dimensions])
    assert {m.file for m in meta.members} >= {"geography_2019", "land_area_and_location_2019"}


def test_written_outputs_load_with_contracts(tmp_path):
    files = {year: _built(year)[0] for year in (2019, 2020)}
    metas = {year: _built(year)[1] for year in (2019, 2020)}
    allowed = allowed_tables(metas)
    write_outputs(files, metas, allowed, tmp_path, internal=INTERNAL)

    loaded = load_topic_files(tmp_path, 2020)
    assert len(loaded) == 47
    assert not any(f.name.startswith(("geography", "land_area")) for f in loaded)
    assert (tmp_path / "2020/internal/geography_2020.yaml").is_file()
    assert (tmp_path / "2019/internal/land_area_and_location_2019.yaml").is_file()
    assert (tmp_path / "2020/census_count_group_quarters_2020.yaml").is_file()
    expected = sorted((f for f in files[2019]
                       if not f.name.startswith(("geography", "land_area"))),
                      key=lambda f: f"{f.name}.yaml")
    assert load_topic_files(tmp_path, 2019) == expected
    assert load_builder_metadata(tmp_path, 2020) == metas[2020]

    raw = json.loads((tmp_path / "allowed_tables.json").read_text())
    assert set(raw) == {"2019", "2020"}
    assert "2020_CBG_B99" not in raw["2020"] and "2019_CBG_PATTERNS" not in raw["2019"]
    for year in ("2019", "2020"):
        assert not any("GEOMETRY" in t or t.endswith("B99") for t in raw[year])
        assert f"{year}_METADATA_CBG_FIPS_CODES" in raw[year]
        assert f"{year}_METADATA_CBG_GEOGRAPHIC_DATA" in raw[year]
    assert "2020_REDISTRICTING_CBG_DATA" in raw["2020"]
    assert load_allowed_tables(tmp_path).tables[2019] == raw["2019"]

    text = "".join(p.read_text() for p in (tmp_path / "2020").glob("*.yaml"))
    for word in ("coverage_sql", "missing_sql", "definition_source",
                 "subject_definitions_page"):
        assert word not in text


def test_topics_yaml_has_no_derived_measures():
    entries = yaml.safe_load(TOPICS.read_text())
    assert not any("derived_measures" in e for e in entries)
