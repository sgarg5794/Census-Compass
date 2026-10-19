import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from census_compass.contracts import (
    AllowedTables,
    BuilderMetadata,
    Outcome,
    QueryRejected,
    TopicFile,
    load_allowed_tables,
    load_builder_metadata,
    load_geography,
    load_topic_files,
)
from census_compass.semantic.race_groups import RACE_GROUPS, race_group

ROOT = Path(__file__).resolve().parents[2]


def test_race_groups_match_spec():
    assert [g.code for g in RACE_GROUPS] == list("ABCDEFGHI")
    assert race_group("E").label == "Native Hawaiian and Other Pacific Islander alone"
    assert "Latinx" in race_group("I").synonyms
    assert race_group("F").synonyms == ()
    assert race_group("H").slug == "white_alone_not_hispanic"


def test_race_group_unknown_code():
    with pytest.raises(KeyError):
        race_group("Z")


def test_topic_file_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        TopicFile(name="a", title="t", description="d", sql_table="s",
                  dimensions=[], measures=[], coverage_sql="x")


@pytest.mark.parametrize("name,model", [("topic_file", TopicFile), ("builder_metadata", BuilderMetadata)])
def test_json_schema_matches_models(name, model):
    schema = json.loads((ROOT / f"definitions/schema/{name}.schema.json").read_text())
    assert schema == model.model_json_schema()


def test_outcome_and_query_rejected():
    assert Outcome.ANSWERED == "answered"
    assert Outcome.QUERY_REJECTED.value == "query_rejected"
    exc = QueryRejected("bad sql")
    assert str(exc) == "bad sql" and exc.reason == "bad sql" and exc.suggestions == []
    assert QueryRejected("x", ["y"]).suggestions == ["y"]


def test_load_geography_keeps_leading_zeros(tmp_path):
    (tmp_path / "geography").mkdir()
    (tmp_path / "geography" / "2020.json").write_text(json.dumps([{"state_abbr": "IL",
        "state_fips": "17", "county_fips": "031", "county": "Cook County", "block_groups": 4002}]))
    assert load_geography(tmp_path, 2020)[0].county_fips == "031"


def test_load_builder_metadata_round_trip(tmp_path):
    (tmp_path / "2020").mkdir()
    meta = {"year": 2020, "members": [], "table_columns": {"T": ["C"]}, "fips_table": "F"}
    (tmp_path / "2020" / "builder_metadata.json").write_text(json.dumps(meta))
    assert load_builder_metadata(tmp_path, 2020).table_columns == {"T": ["C"]}


def test_load_allowed_tables_converts_year_keys(tmp_path):
    (tmp_path / "allowed_tables.json").write_text(json.dumps({"2020": ["A", "B"]}))
    result = load_allowed_tables(tmp_path)
    assert isinstance(result, AllowedTables)
    assert result.tables == {2020: ["A", "B"]}


def test_load_topic_files_sorted(tmp_path):
    (tmp_path / "2020").mkdir()
    (tmp_path / "2020" / "builder_metadata.json").write_text("{}")
    for n in ("b", "a"):
        (tmp_path / "2020" / f"{n}.yaml").write_text(yaml.safe_dump(
            {"name": n, "title": n, "description": "d", "sql_table": "s",
             "dimensions": [], "measures": []}))
    assert [t.name for t in load_topic_files(tmp_path, 2020)] == ["a", "b"]


@pytest.mark.parametrize("call", [
    lambda p: load_builder_metadata(p, 2020),
    lambda p: load_allowed_tables(p),
    lambda p: load_topic_files(p, 2020),
    lambda p: load_geography(p, 2020),
])
def test_loaders_missing_file(tmp_path, call):
    with pytest.raises(FileNotFoundError, match=str(tmp_path)):
        call(tmp_path)
