from pathlib import Path

import pytest
import yaml

from census_compass.semantic import index as index_mod
from census_compass.semantic.index import build_index
from census_compass.semantic.retrieval import search as _search
from census_compass.tools.schema import _get_schema, _search_schema, get_schema, search_schema

MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _m(name, title, desc, pg=None, kind="measure"):
    d = {"name": name, "title": title, "description": desc, "sql": "x", "type": "number" if kind == "measure" else "string"}
    if kind == "measure":
        d.update(type="sum", format="number")
    if pg:
        d["meta"] = {"population_group": pg}
    return d


def _topic(name, title, desc, measures, dims=()):
    return {"name": name, "title": title, "description": desc, "sql_table": "T",
            "dimensions": list(dims), "measures": measures}


def _write(root: Path, year: int, topic: dict, sub: str = ""):
    d = root / str(year) / sub
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{topic['name']}.yaml").write_text(yaml.safe_dump(topic))


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    root = tmp_path_factory.mktemp("sem")
    for y in (2019, 2020):
        _write(root, y, _topic(f"poverty_{y}", "Poverty status", "People below the poverty line",
                               [_m("poverty_rate", "Poverty rate", "Share of people in poverty")]))
        _write(root, y, _topic(f"income_{y}", "Household income", "Household income distribution",
                               [_m("household_income", "Household income", "Total household income"),
                                _m("household_income_asian_alone", "Household income Asian alone",
                                   "Household income for Asian alone householders", "D"),
                                _m("household_income_black_alone", "Household income Black alone",
                                   "Household income for Black alone householders", "B")]))
        _write(root, y, _topic(f"unemployment_{y}", "Unemployment", "Employment status and unemployed civilians",
                               [_m("unemployed", "Unemployed", "Civilians unemployed in labor force")]))
    _write(root, 2020, _topic("geography_2020", "Geography county", "County names and boundaries",
                              [_m("county_count", "County", "County")]), sub="internal")
    idx = root / "index"
    build_index(root, idx, MODEL)
    return root, idx


def search(env, q, year, pg):
    return _search(q, year, pg, index_dir=env[1], model_name=MODEL)


def test_year_filter(env):
    hits = search(env, "unemployment", 2020, None)
    assert hits and all(h.name.endswith("_2020") for h in hits)


def test_race_filter_excludes_other_races(env):
    hits = search(env, "household income", 2020, "D")
    assert hits
    names = [m.name for h in hits for m in h.matched_members]
    assert not any(n.endswith("black_alone") for n in names)
    income = [m.name for h in hits if h.name == "income_2020" for m in h.matched_members]
    assert all(n.endswith(("asian_alone", "income")) for n in income)
    assert "household_income_asian_alone" in names


def test_race_query_without_race_measure_returns_general_topic(env):
    assert any(h.name == "poverty_2020" for h in search(env, "poverty rate", 2020, "D"))


def test_internal_files_not_indexed(env):
    assert not any("geography" in h.name for h in search(env, "county", 2020, None))


def test_get_schema_unknown_name(env):
    out = _get_schema(["nope_2020", "poverty_2020"], semantic_dir=env[0])
    assert out["errors"][0]["name"] == "nope_2020" and out["errors"][0]["valid_names"]
    assert "geography_2020" not in out["errors"][0]["valid_names"]
    assert out["files"][0]["name"] == "poverty_2020"


def test_empty_and_nonsense_queries_never_raise(env):
    assert search(env, "   ", 2020, None) == []
    hits = search(env, "zzzz qqqq", 2020, None)
    assert len(hits) <= 5


def test_hit_shape_and_limits(env):
    hits = search(env, "household income", 2020, None)
    assert len(hits) <= 5 and all(len(h.matched_members) <= 5 for h in hits)
    assert hits[0].name == "income_2020" and hits[0].description


def test_tool_wrappers(env, monkeypatch):
    import inspect
    assert list(inspect.signature(search_schema).parameters) == ["query", "year", "population_group"]
    assert list(inspect.signature(get_schema).parameters) == ["names"]
    out = _search_schema("poverty", 2020, index_dir=env[1], model_name=MODEL)
    assert out["results"][0]["name"] == "poverty_2020"


def test_atomic_swap_keeps_old_index_on_failure(env, tmp_path, monkeypatch):
    root, _ = env
    idx = tmp_path / "index"
    build_index(root, idx, MODEL)
    before = sorted(p.name for p in idx.rglob("*"))
    orig = index_mod._write_table

    def boom(db, name, rows, model_name):
        if name == "members":
            raise RuntimeError("boom")
        return orig(db, name, rows, model_name)

    monkeypatch.setattr(index_mod, "_write_table", boom)
    with pytest.raises(RuntimeError):
        build_index(root, idx, MODEL)
    assert sorted(p.name for p in idx.rglob("*")) == before
    assert not (tmp_path / "index.tmp").exists()
    assert _search("poverty", 2020, None, index_dir=idx, model_name=MODEL)
