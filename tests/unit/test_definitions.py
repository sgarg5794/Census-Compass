"""The v1 migration must not lose columns or authorize invalid calculations."""

import pytest

from census_compass.semantic import catalog, definitions


def raw_table(name="2020_CBG_B01"):
    def field(code, kind="estimate", description="Population", title="Sex By Age"):
        return {
            "name": code, "sql": f'"{code}"', "data_type": "FLOAT", "nullable": True,
            "metadata_status": "available", "description": description,
            "is_identifier": False, "statistic_kind": kind, "field_path": [
                "Estimate" if kind == "estimate" else "MarginOfError", title,
                "Total population", "Total",
            ], "universe": "Total population",
            "metadata_source": {"table": "2020_METADATA_CBG_FIELD_DESCRIPTIONS"},
            "source_metadata": {"TABLE_NUMBER": code.split("e")[0].split("m")[0],
                                "TABLE_TITLE": title},
        }
    return {
        "name": name, "description": "Source-derived table description",
        "source_table": f'"DB"."PUBLIC"."{name}"', "object_type": "BASE TABLE",
        "family": "demographic", "year_label": 2020,
        "columns": {
            "CENSUS_BLOCK_GROUP": {"name": "CENSUS_BLOCK_GROUP", "sql": '"CENSUS_BLOCK_GROUP"',
                "data_type": "TEXT", "nullable": True, "metadata_status": "missing",
                "is_identifier": True, "description": None, "statistic_kind": None,
                "field_path": [], "metadata_source": None, "source_metadata": None},
            "B01001e1": field("B01001e1"),
            "B01001m1": field("B01001m1", "margin_of_error", "Population error"),
            "B01002e1": field("B01002e1", description="Median age", title="Median Age By Sex"),
            "B01002m1": field("B01002m1", "margin_of_error", "Median age error", "Median Age By Sex"),
            "B01002Ee1": {"name": "B01002Ee1", "sql": '"B01002Ee1"', "data_type": "FLOAT",
                "nullable": True, "metadata_status": "missing", "is_identifier": False,
                "description": None, "statistic_kind": None, "field_path": [],
                "metadata_source": None, "source_metadata": None},
        },
    }


def convert(tables=None, audit=()):
    assert callable(getattr(definitions, "convert_catalog", None)), "v1 converter not implemented"
    return definitions.convert_catalog(
        {"version": 1, "source": {"extracted_at": "2026-10-18"},
         "tables": tables or {"2020_CBG_B01": raw_table()}}, list(audit)
    )


def source_columns(document):
    result = set(document["unresolved_columns"])
    for section in ("dimensions", "measures"):
        for field in document[section].values():
            result.add(field["metadata"]["source_column"])
            if "margin_of_error" in field:
                result.add(field["margin_of_error"]["metadata"]["source_column"])
    return result


def test_v1_partitions_every_column_and_pairs_uncertainty():
    result = convert()["tables"]["2020_CBG_B01"]
    assert result["name"] == "population_age_and_sex_2020"
    assert source_columns(result) == set(raw_table()["columns"])
    assert "columns" not in result
    assert result["dimensions"]["census_block_group"]["sql"] == '"CENSUS_BLOCK_GROUP"'
    population = result["measures"]["population"]
    assert population["sql"] == '"B01001e1"'
    assert population["margin_of_error"]["sql"] == '"B01001m1"'
    assert set(population["aggregation"]["allowed"]) == {"sum", "avg", "min", "max"}
    assert "default" not in population["aggregation"]
    assert population["margin_of_error"]["aggregation"]["allowed"] == {}


def test_medians_cannot_inherit_count_aggregations():
    document = convert()["tables"]["2020_CBG_B01"]
    median = next(m for m in document["measures"].values() if m["sql"] == '"B01002e1"')
    assert median["aggregation"]["allowed"] == {}
    assert median["unit"] == "years"
    assert median["constraints"]["allowed_result_grains"] == ["census_block_group"]
    assert document["unresolved_columns"]["B01002Ee1"]["queryable"] is False
    assert document["unresolved_columns"]["B01002Ee1"]["description"] is None


def test_other_measures_are_not_assigned_population_count_rules():
    table = raw_table("2020_CBG_B19")
    table["columns"] = {"B19013e1": {
        **table["columns"]["B01001e1"], "name": "B19013e1", "sql": '"B19013e1"',
        "description": "Median household income",
        "source_metadata": {"TABLE_NUMBER": "B19013", "TABLE_TITLE": "Median Household Income"},
    }}
    measure = next(iter(convert({table["name"]: table})["tables"][table["name"]]["measures"].values()))
    assert measure["aggregation"]["allowed"] == {}
    assert measure["metadata"]["aggregation_status"] == "requires_measure_specific_review"


def test_uncertainty_without_an_estimate_is_retained_without_rollup():
    table = raw_table()
    del table["columns"]["B01001e1"]
    doc = convert({table["name"]: table})["tables"][table["name"]]
    assert source_columns(doc) == set(table["columns"])
    error = next(m for m in doc["measures"].values() if m["sql"] == '"B01001m1"')
    assert error["aggregation"]["allowed"] == {}
    assert error["metadata"]["statistic_kind"] == "margin_of_error"


def test_join_coverage_and_direction_are_preserved():
    a, b = raw_table(), raw_table("2020_CBG_B19")
    audit = [{"JOIN_FAMILY": "CBG", "DATA_YEAR": "2020", "TABLE_A": a["name"],
              "TABLE_B": b["name"], "A_NULL_KEYS": "0", "B_NULL_KEYS": "0",
              "A_DUPLICATE_KEY_ROWS": "0", "B_DUPLICATE_KEY_ROWS": "0",
              "A_KEYS_MISSING_FROM_B": "2", "B_KEYS_MISSING_FROM_A": "4",
              "OBSERVED_RELATIONSHIP": "ONE_TO_ONE_ON_MATCHING_KEYS",
              "MATCHING_DISTINCT_KEYS": "100", "INNER_JOIN_ROWS": "100"}]
    result = convert({a["name"]: a, b["name"]: b}, audit)["tables"]
    forward = next(iter(result[a["name"]]["relationships"].values()))
    reverse = next(iter(result[b["name"]]["relationships"].values()))
    assert forward["metadata"]["coverage"]["source_keys_without_target"] == 2
    assert reverse["metadata"]["coverage"]["source_keys_without_target"] == 4
    assert forward["cardinality"] == "one_to_one"
    assert result[a["name"]]["grain"]["primary_key"] == ["census_block_group"]


def test_loader_accepts_physical_and_logical_names_without_loading_other_tables(tmp_path):
    converted = convert()
    catalog.write_table_schemas(converted, tmp_path)
    result = catalog.load_table_schema("2020_CBG_B01", tmp_path)
    assert result["name"] == "population_age_and_sex_2020"
    same = catalog.load_table_schema("population_age_and_sex_2020", tmp_path)
    assert same == result


def test_duplicate_source_column_representation_is_rejected():
    assert callable(getattr(definitions, "physical_columns", None)), "Coverage validator missing"
    doc = {"dimensions": {"a": {"metadata": {"source_column": "ID"}}},
           "measures": {"b": {"metadata": {"source_column": "ID"}}},
           "unresolved_columns": {}}
    with pytest.raises(ValueError, match="Duplicate"):
        definitions.physical_columns(doc)
