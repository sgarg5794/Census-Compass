import logging
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from census_compass.config import get_settings
from census_compass.tools import glossary
from census_compass.tools.glossary import (
    GlossaryEntry,
    load_glossary,
    lookup_glossary,
    validate_glossary,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "glossary_small.yaml"
URL = "https://www.census.gov/x"


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def use_fixture(monkeypatch):
    monkeypatch.setenv("GLOSSARY_PATH", str(FIXTURE))


def _entry(term="T", aliases=(), definition="One sentence.", **source):
    src = {"title": "Doc", "page": 1, "url": URL} | source
    return GlossaryEntry(term=term, aliases=list(aliases), definition=definition, source=src)


def _write(path: Path, rows: list[dict]) -> Path:
    path.write_text(yaml.safe_dump(rows))
    return path


def _row(term, aliases=(), definition="Def."):
    return {
        "term": term,
        "aliases": list(aliases),
        "definition": definition,
        "source": {"title": "Doc", "page": 1, "url": URL},
    }


def test_lookup_matches_term_and_alias_case_insensitive(use_fixture):
    by_term = lookup_glossary("What does MARGIN OF ERROR mean?")
    assert [e["term"] for e in by_term["entries"]] == ["Margin of error"]
    assert by_term["entries"][0]["source"] == {
        "title": "ACS General Handbook 2020",
        "page": 49,
        "url": "https://www.census.gov/programs-surveys/acs/library/handbooks/general.html",
    }
    assert "aliases" not in by_term["entries"][0]
    by_alias = lookup_glossary("what is the moe?")
    assert [e["term"] for e in by_alias["entries"]] == ["Margin of error"]


def test_lookup_returns_at_most_three_longest_match_first(tmp_path, monkeypatch):
    rows = [
        _row("alpha"),
        _row("beta"),
        _row("gamma delta epsilon"),
        _row("zeta"),
        _row("eta theta"),
    ]
    monkeypatch.setenv("GLOSSARY_PATH", str(_write(tmp_path / "g.yaml", rows)))
    out = lookup_glossary("alpha beta gamma delta epsilon zeta eta theta")
    assert [e["term"] for e in out["entries"]] == ["gamma delta epsilon", "eta theta", "alpha"]


def test_unknown_question_returns_empty_with_message(use_fixture):
    out = lookup_glossary("What is the airspeed of a swallow?")
    assert out == {
        "entries": [],
        "message": "No entry in the Census reference material matches this question.",
    }


def test_missing_file_returns_unavailable_message(tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "nope.yaml"))
    with caplog.at_level(logging.ERROR):
        out = lookup_glossary("median")
    assert out == {"entries": [], "message": "The Census reference material is unavailable."}
    assert any("FileNotFoundError" in r.getMessage() for r in caplog.records)


def test_invalid_file_returns_unavailable_message(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("- term: [unclosed\n")
    monkeypatch.setenv("GLOSSARY_PATH", str(bad))
    assert lookup_glossary("median")["message"] == "The Census reference material is unavailable."


def test_substring_does_not_match(use_fixture):
    for q in ("an immediate answer", "medianly speaking", "the immedian"):
        assert lookup_glossary(q)["entries"] == []
    assert [e["term"] for e in lookup_glossary("median?")["entries"]] == ["Median"]


def test_cache_refreshes_when_file_changes(tmp_path):
    p = _write(tmp_path / "g.yaml", [_row("one")])
    assert [e.term for e in load_glossary(p)] == ["one"]
    _write(p, [_row("two")])
    import os

    st = p.stat()
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert [e.term for e in load_glossary(p)] == ["two"]


def test_validate_glossary_flags_each_error_class():
    assert validate_glossary(load_glossary(FIXTURE)) == []
    entries = [
        _entry("Dup", aliases=["x"]),
        _entry("dup"),
        _entry("Other", aliases=["X"]),
        _entry("Blank", definition="   "),
        _entry("NoTitle", title=" "),
        _entry("NoPage", page=""),
        _entry("NoUrl", url=""),
        _entry("BadUrl", url="https://example.com/a"),
        _entry("Long", definition="One. Two! Three? Four. Five."),
    ]
    errors = validate_glossary(entries)
    for needle in ("duplicate", "Blank", "NoTitle", "NoPage", "NoUrl", "BadUrl", "Long"):
        assert any(needle in e for e in errors), needle
    assert sum("duplicate" in e for e in errors) >= 2
    assert not any("'T'" in e for e in errors)


def test_validate_accepts_four_sentences_and_www2_url():
    ok = _entry("Ok", definition="One. Two. Three. Four.", url="https://www2.census.gov/a.pdf")
    assert validate_glossary([ok]) == []


def test_load_glossary_rejects_extra_fields(tmp_path):
    row = _row("a") | {"extra": 1}
    with pytest.raises(ValidationError):
        load_glossary(_write(tmp_path / "g.yaml", [row]))


def test_module_exposes_logger():
    assert glossary.logger.name == "census_compass.tools.glossary"
