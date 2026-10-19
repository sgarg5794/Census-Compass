import pytest

from census_compass.semantic.race_groups import RACE_GROUPS
from census_compass.tools.population_group import resolve_population_group

_LABELS = [g.label for g in RACE_GROUPS]
# "White" alone is ambiguous by design (A vs H); every other wording maps to one code.
_CASES = [
    (name, g.code)
    for g in RACE_GROUPS
    for name in (g.label, g.slug, *g.synonyms)
    if name != "White"
]


@pytest.mark.parametrize("text,code", _CASES)
def test_every_label_slug_synonym_resolves(text, code):
    r = resolve_population_group(text)
    assert r["status"] == "ok"
    assert r["code"] == code
    assert r["label"] == next(g.label for g in RACE_GROUPS if g.code == code)


@pytest.mark.parametrize(
    "text,code",
    [
        ("  BLACK ", "B"),
        ("black people", "B"),
        ("African Americans", "B"),
        ("Hispanic households", "I"),
        ("Latina", "I"),
        ("Latinx residents", "I"),
        ("American Indian", "C"),
        ("Alaska Native", "C"),
        ("Asian population", "D"),
        ("Pacific Islander", "E"),
        ("non-hispanic white", "H"),
        ("Two or more races", "G"),
    ],
)
def test_wording_variants(text, code):
    r = resolve_population_group(text)
    assert (r["status"], r["code"]) == ("ok", code)


@pytest.mark.parametrize("text", ["White", "white people", "WHITE"])
def test_white_is_ambiguous(text):
    r = resolve_population_group(text)
    assert r["status"] == "ambiguous"
    assert r["code"] is None
    assert r["candidates"] == ["White alone", "White alone, not Hispanic or Latino"]
    assert "Hispanic" in r["message"]


@pytest.mark.parametrize(
    "text,suggested",
    [
        ("Asian alone or in combination", "Asian alone"),
        ("black alone or in combination", "Black or African American alone"),
        ("White alone or in combination", "White alone"),
    ],
)
def test_alone_or_in_combination_not_found(text, suggested):
    r = resolve_population_group(text)
    assert r["status"] == "not_found"
    assert r["code"] is None
    assert r["candidates"] == [suggested]
    assert "alone" in r["message"]


@pytest.mark.parametrize("text", [None, "", "   "])
def test_empty_means_no_group(text):
    r = resolve_population_group(text)
    assert r["status"] == "not_found"
    assert r["candidates"] == []
    assert "no population group" in r["message"].lower()


def test_unknown_lists_all_labels():
    r = resolve_population_group("Martian")
    assert r["status"] == "not_found"
    assert r["candidates"] == _LABELS
