import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEV = ROOT / "evaluations" / "search_dev.yaml"
HELD = ROOT / "evaluations" / "search_heldout.yaml"
TOPICS = ROOT / "docs" / "specs" / "2026-10-18-topics-draft.yaml"
LETTERS = set("ABCDEFGHI")


def _load(path):
    return yaml.safe_load(path.read_text())


def _script():
    spec = importlib.util.spec_from_file_location(
        "select_embedding_model", ROOT / "scripts" / "select_embedding_model.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _valid_files():
    names = set()
    for t in _load(TOPICS):
        if t.get("internal"):
            continue
        if t["name"].endswith("_2020"):
            names.add(t["name"])
        else:
            names |= {f"{t['name']}_2019", f"{t['name']}_2020"}
    return names


def _words(q):
    return frozenset("".join(c if c.isalnum() else " " for c in q.lower()).split())


@pytest.fixture(scope="module")
def sets():
    return _load(DEV), _load(HELD)


def test_sizes(sets):
    dev, held = sets
    assert len(dev) >= 40
    assert len(held) >= 60


def test_entries_wellformed(sets):
    valid = _valid_files()
    for entry in sum(sets, []):
        assert set(entry) == {"id", "question", "year", "population_group", "required_files"}
        assert entry["year"] in (2019, 2020)
        assert entry["population_group"] is None or entry["population_group"] in LETTERS
        assert entry["required_files"]
        for f in entry["required_files"]:
            assert f in valid, f
            if not f.startswith("census_count"):
                assert f.endswith(f"_{entry['year']}"), (entry["id"], f)
            else:
                assert entry["year"] == 2020


def test_independent_and_unique(sets):
    dev, held = sets
    for s in (dev, held):
        assert len({e["id"] for e in s}) == len(s)
    assert not {e["id"] for e in dev} & {e["id"] for e in held}
    qs = [e["question"] for e in dev + held]
    assert len({q.lower().strip() for q in qs}) == len(qs)
    ws = [_words(q) for q in qs]
    assert len(set(ws)) == len(ws)


def test_both_years_and_races(sets):
    for s in sets:
        assert {e["year"] for e in s} == {2019, 2020}
        assert sum(e["population_group"] is not None for e in s) >= 3
        assert any(len(e["required_files"]) > 1 for e in s)


def test_heldout_covers_most_topics(sets):
    _, held = sets
    topics = {
        f.rsplit("_", 1)[0]
        if f[-5:] in ("_2019", "_2020") and not f.startswith("census_count")
        else f
        for e in held
        for f in e["required_files"]
    }
    assert len(topics) >= 30


def test_recall_computation():
    mod = _script()
    questions = [
        {"id": "a", "year": 2019, "required_files": ["x_2019", "y_2019"]},
        {"id": "b", "year": 2019, "required_files": ["z_2019"]},
        {"id": "c", "year": 2020, "required_files": ["x_2020"]},
    ]
    results = {"a": ["x_2019", "y_2019", "q"], "b": ["q", "r"], "c": ["x_2020"]}
    out = mod.compute_recall(questions, results)
    assert out["overall"] == pytest.approx(2 / 3)
    assert out["by_year"] == {2019: 0.5, 2020: 1.0}
    assert [m["id"] for m in out["misses"]] == ["b"]
    assert out["misses"][0]["missing"] == ["z_2019"]


def test_recall_only_top_k():
    mod = _script()
    q = [{"id": "a", "year": 2019, "required_files": ["x"]}]
    assert mod.compute_recall(q, {"a": ["1", "2", "3", "4", "5", "x"]})["overall"] == 0.0


def test_heldout_guard():
    mod = _script()
    with pytest.raises(SystemExit):
        mod.parse_args(["--candidates", "m", "--set", "evaluations/search_heldout.yaml"])
    args = mod.parse_args(
        ["--candidates", "m", "--set", "evaluations/search_heldout.yaml", "--acceptance"]
    )
    assert args.acceptance
    args = mod.parse_args(["--candidates", "m1", "m2"])
    assert args.candidates == ["m1", "m2"]
    assert args.set.endswith("search_dev.yaml")


def test_census_count_not_mixed_with_acs_demographics(sets):
    """Rule C: a 2020 question answerable from the Census count requires only the
    census_count topic, not the ACS population/race/Hispanic topic, unless the
    question also asks about income, age, housing, or explicitly about the survey/ACS."""
    acs = {"hispanic_origin_2020", "race_2020", "population_age_and_sex_2020"}
    allowed = ("income", "age", "housing", "survey", "acs", "american community")
    for e in sum(sets, []):
        files = set(e["required_files"])
        if e["year"] == 2020 and any(f.startswith("census_count") for f in files):
            if files & acs:
                assert any(w in e["question"].lower() for w in allowed), e["id"]


def test_2020_demographic_only_questions_use_census_count(sets):
    """Both cases covered: an ACS-worded 2020 demographic question keeps the ACS topic."""
    _, held = sets
    survey = [
        e
        for e in held
        if e["year"] == 2020 and "american community survey" in e["question"].lower()
    ]
    assert survey and all(
        not any(f.startswith("census_count") for f in e["required_files"]) for e in survey
    )
