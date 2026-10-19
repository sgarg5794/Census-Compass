"""Semantic extraction must preserve meanings and expose missing definitions."""

import pytest

from census_compass.semantic import catalog


def column(table, name, position=1, comment=None):
    return {
        "TABLE_NAME": table, "COLUMN_NAME": name, "ORDINAL_POSITION": position,
        "DATA_TYPE": "FLOAT", "IS_NULLABLE": "YES", "COMMENT": comment,
    }


def metadata(code="B01001e3", year=2020):
    return {
        "TABLE_ID": code, "TABLE_NUMBER": "B01001", "TABLE_TITLE": "Sex By Age",
        "TABLE_TOPICS": "Age and Sex", "TABLE_UNIVERSE": "Total population",
        "FIELD_LEVEL_1": "Estimate", "FIELD_LEVEL_2": "SEX BY AGE",
        "FIELD_LEVEL_3": "Total population", "FIELD_LEVEL_4": "Total",
        "FIELD_LEVEL_5": "Male", "FIELD_LEVEL_6": "Under 5 years",
        "FIELD_LEVEL_7": None, "FIELD_LEVEL_8": "", "FIELD_LEVELl_9": None,
        "FIELD_LEVEL_10": None,
    }


def snapshot(columns, dictionaries=None, extra_tables=()):
    names = sorted({c["TABLE_NAME"] for c in columns} | set(extra_tables))
    return {
        "database": "CENSUS_DB", "schema": "PUBLIC",
        "extracted_at": "2026-10-18T00:00:00+00:00",
        "tables": [{"TABLE_NAME": name, "TABLE_TYPE": "BASE TABLE", "COMMENT": None}
                   for name in names],
        "columns": columns, "dictionaries": dictionaries or {},
    }


def build(data):
    assert callable(getattr(catalog, "build_catalog", None)), "Catalog builder not implemented"
    return catalog.build_catalog(data)


def test_field_hierarchy_preserves_source_and_exact_sql_identifier():
    source = metadata()
    result, missing = build(snapshot(
        [column("2020_CBG_B01", "B01001e3")],
        {"2020_METADATA_CBG_FIELD_DESCRIPTIONS": [source]},
    ))
    entry = result["tables"]["2020_CBG_B01"]["columns"]["B01001e3"]
    assert entry["description"] == (
        "Estimate. Sex By Age. Universe: Total population. Breakdown: Total > Male > Under 5 years."
    )
    assert entry["sql"] == '\"B01001e3\"'
    assert entry["source_metadata"] == source
    assert entry["metadata_source"]["table"].endswith('"2020_METADATA_CBG_FIELD_DESCRIPTIONS"')
    assert entry["semantic_type"] == "measure"
    assert entry["aggregation"] is None
    assert missing == []


def test_same_field_code_uses_matching_year_not_other_year_definition():
    earlier = metadata("B19013e1")
    earlier["TABLE_TITLE"] = "Income in 2019 dollars"
    later = metadata("B19013e1")
    later["TABLE_TITLE"] = "Income in 2020 dollars"
    result, _ = build(snapshot(
        [column("2019_CBG_B19", "B19013e1"), column("2020_CBG_B19", "B19013e1")],
        {"2019_METADATA_CBG_FIELD_DESCRIPTIONS": [earlier],
         "2020_METADATA_CBG_FIELD_DESCRIPTIONS": [later]},
    ))
    assert "2019 dollars" in result["tables"]["2019_CBG_B19"]["columns"]["B19013e1"]["description"]
    assert "2020 dollars" in result["tables"]["2020_CBG_B19"]["columns"]["B19013e1"]["description"]


def test_missing_columns_stay_unresolved_and_are_not_embedding_eligible():
    result, missing = build(snapshot([
        column("2020_CBG_B01", "B01002Ee1"),
        column("2020_CBG_B01", "CENSUS_BLOCK_GROUP", 2),
        column("2020_METADATA_CBG_FIPS_CODES", "COUNTY_FIPS"),
    ]))
    entries = result["tables"]["2020_CBG_B01"]["columns"]
    assert entries["B01002Ee1"]["description"] is None
    assert entries["B01002Ee1"]["metadata_status"] == "missing"
    assert not entries["B01002Ee1"]["embedding_eligible"]
    assert entries["CENSUS_BLOCK_GROUP"]["is_identifier"]
    reasons = {r["column_name"]: r["reason"] for r in missing}
    assert reasons["B01002Ee1"] == "missing_demographic_dictionary_entry"
    assert reasons["CENSUS_BLOCK_GROUP"] == "identifier_not_documented_by_field_dictionary"
    assert reasons["COUNTY_FIPS"] == "identifier_not_documented_by_field_dictionary"


def test_redistricting_matches_column_id_and_retains_universe():
    row = {"COLUMN_ID": "P0010003", "FIELD_NAME": "White alone",
           "COLUMN_TOPIC": "RACE", "COLUMN_UNIVERSE": "Total population"}
    result, missing = build(snapshot(
        [column("2020_REDISTRICTING_CBG_DATA", "P0010003")],
        {"2020_REDISTRICTING_METADATA_CBG_FIELD_DESCRIPTIONS": [row]},
    ))
    field = result["tables"]["2020_REDISTRICTING_CBG_DATA"]["columns"]["P0010003"]
    assert field["description"] == "White alone. Topic: RACE. Universe: Total population."
    assert field["metadata_source"]["match_column"] == "COLUMN_ID"
    assert field["universe"] == "Total population"
    assert field["aggregation"] is None
    assert missing == []


def test_column_comment_is_used_with_explicit_provenance():
    result, missing = build(snapshot([
        column("2020_SUPPORT", "SOME_FIELD", comment="Provider definition")
    ]))
    field = result["tables"]["2020_SUPPORT"]["columns"]["SOME_FIELD"]
    assert field["description"] == "Provider definition"
    assert field["metadata_status"] == "column_comment"
    assert field["metadata_source"]["match_column"] == "COMMENT"
    assert missing == []


def test_duplicate_dictionary_codes_are_rejected_instead_of_silently_selected():
    with pytest.raises(ValueError, match="Duplicate"):
        build(snapshot(
            [column("2020_CBG_B01", "B01001e3")],
            {"2020_METADATA_CBG_FIELD_DESCRIPTIONS": [metadata(), metadata()]},
        ))


def test_catalog_includes_empty_tables_and_does_not_drop_columns():
    result, missing = build(snapshot(
        [column("2020_SUPPORT", "UNKNOWN")], extra_tables=["2020_EMPTY"],
    ))
    assert set(result["tables"]) == {"2020_SUPPORT", "2020_EMPTY"}
    assert result["tables"]["2020_EMPTY"]["columns"] == {}
    assert len(missing) == 1


def test_field_level_nine_spelling_and_margin_of_error_are_preserved():
    row = metadata("B01001m3")
    row["FIELD_LEVEL_1"] = "MarginOfError"
    row["FIELD_LEVELl_9"] = "Source detail"
    result, _ = build(snapshot(
        [column("2020_CBG_B01", "B01001m3")],
        {"2020_METADATA_CBG_FIELD_DESCRIPTIONS": [row]},
    ))
    entry = result["tables"]["2020_CBG_B01"]["columns"]["B01001m3"]
    assert entry["statistic_kind"] == "margin_of_error"
    assert entry["field_path"][-1] == "Source detail"
    assert entry["aggregation"] is None
