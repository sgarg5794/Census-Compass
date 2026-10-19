"""Each schema can be loaded without reading other tables."""

import pytest
import yaml

from census_compass.semantic import catalog


def functions():
    assert callable(getattr(catalog, "write_table_schemas", None)), "Per-table writer missing"
    assert callable(getattr(catalog, "load_table_schema", None)), "Single-table loader missing"
    return catalog.write_table_schemas, catalog.load_table_schema


def test_split_files_preserve_fields_and_load_only_requested_table(tmp_path):
    write, load = functions()
    source = {"database": "DB", "schema": "PUBLIC"}
    data = {"version": 1, "source": source, "tables": {
        "2020_CBG_B01": {"name": "2020_CBG_B01", "columns": {
            "B01001e3": {"description": "Males under five", "sql": '\"B01001e3\"'},
        }},
        "2020_CBG_B19": {"name": "2020_CBG_B19", "columns": {}},
    }}
    written = write(data, tmp_path)
    assert {path.name for path in written} == {"2020_CBG_B01.yaml", "2020_CBG_B19.yaml"}
    # An unreadable unrelated schema must not prevent loading the requested one.
    (tmp_path / "2020_CBG_B19.yaml").write_text("broken: [")
    result = load("2020_CBG_B01", tmp_path)
    assert result["source"] == source
    assert result["columns"]["B01001e3"]["description"] == "Males under five"
    assert "tables" not in result
    assert not (tmp_path / "catalog.yaml").exists()


@pytest.mark.parametrize("name", ["../secret", "/etc/passwd", "x/y", ""])
def test_loader_rejects_names_that_escape_schema_directory(tmp_path, name):
    _, load = functions()
    with pytest.raises(ValueError, match="table name"):
        load(name, tmp_path)


def test_loader_checks_filename_matches_embedded_table_identity(tmp_path):
    _, load = functions()
    (tmp_path / "2020_CBG_B01.yaml").write_text(yaml.safe_dump({"name": "WRONG_TABLE"}))
    with pytest.raises(ValueError, match="identity"):
        load("2020_CBG_B01", tmp_path)


def test_loader_reports_missing_table_without_falling_back_to_another(tmp_path):
    _, load = functions()
    with pytest.raises(FileNotFoundError):
        load("2020_NOT_FOUND", tmp_path)
