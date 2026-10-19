"""Content checks for the reviewed Census glossary (definitions/source/census_glossary.yaml)."""

from pathlib import Path

import pytest

from census_compass.config import get_settings
from census_compass.tools.glossary import load_glossary, lookup_glossary, validate_glossary

GLOSSARY = Path(__file__).parents[2] / "definitions" / "source" / "census_glossary.yaml"

OFFICIAL_TITLES = {
    "ACS Subject Definitions 2020",
    "ACS General Handbook 2020 — Chapter 2",
    "ACS General Handbook 2020 — Chapter 3",
    "ACS General Handbook 2020 — Chapter 4",
    "ACS General Handbook 2020 — Chapter 7",
    "ACS General Handbook 2020 — Chapter 8",
    "ACS General Handbook 2020 — Chapter 9",
    "Geographic Areas Reference Manual — Chapter 4",
    "ACS Design and Methodology (January 2014) — Chapter 13",
}

REQUIRED_TERMS = [
    "median",
    "mean",
    "aggregate",
    "margin of error",
    "confidence level",
    "estimate",
    "American Community Survey",
    "5-year estimates",
    "Decennial Census",
    "universe",
    "group quarters",
    "household",
    "family",
    "householder",
    "household income",
    "per capita income",
    "earnings",
    "labor force",
    "civilian labor force",
    "employed",
    "unemployed",
    "unemployment rate",
    "labor force participation rate",
    "poverty status",
    "poverty threshold",
    "tenure",
    "owner-occupied",
    "renter-occupied",
    "vacancy rate",
    "gross rent",
    "occupants per room",
    "Hispanic or Latino origin",
    "race",
    "educational attainment",
    "health insurance coverage",
    "limited English speaking household",
    "block group",
    "census tract",
    "county",
    "FIPS code",
    "top-coded",
]


@pytest.fixture(autouse=True)
def _glossary_path(monkeypatch):
    monkeypatch.setenv("GLOSSARY_PATH", str(GLOSSARY))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_glossary_file_valid():
    assert validate_glossary(load_glossary(GLOSSARY)) == []


def test_at_least_forty_entries():
    assert len(load_glossary(GLOSSARY)) >= 40


def test_required_terms_covered():
    phrases = set()
    for entry in load_glossary(GLOSSARY):
        phrases.add(entry.term.casefold())
        phrases.update(a.casefold() for a in entry.aliases)
    missing = [t for t in REQUIRED_TERMS if t.casefold() not in phrases]
    assert missing == []


def test_sources_are_official_documents():
    for entry in load_glossary(GLOSSARY):
        assert entry.source.title in OFFICIAL_TITLES, entry.term


def test_lookup_finds_median_with_page_141():
    result = lookup_glossary("what does median mean?")
    medians = [e for e in result["entries"] if e["term"] == "Median"]
    assert medians and str(medians[0]["source"]["page"]) == "141"


def test_plural_questions_match():
    assert lookup_glossary("what are medians?")["entries"]
    assert lookup_glossary("what are households?")["entries"]
    assert lookup_glossary("what are estimates?")["entries"]
