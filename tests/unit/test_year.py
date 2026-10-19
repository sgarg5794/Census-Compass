import pytest

from census_compass.tools.year import resolve_year


@pytest.mark.parametrize(
    "text,status,years,comparison",
    [
        (None, "ok", [2020], False),
        ("latest", "ok", [2020], False),
        ("current", "ok", [2020], False),
        ("2019", "ok", [2019], False),
        ("2020", "ok", [2020], False),
        ("2016", "ambiguous", [], False),
        ("2017", "ambiguous", [], False),
        ("2018", "ambiguous", [], False),
        ("2015", "ok", [2019], False),
        ("2010", "unavailable", [], False),
        ("2023", "unavailable", [], False),
        ("from 2019 to 2020", "ok", [2019, 2020], True),
    ],
)
def test_year_rules(text, status, years, comparison):
    r = resolve_year(text)
    assert r["status"] == status
    assert [p["year"] for p in r["periods"]] == years
    assert r["comparison"] is comparison
    if status != "ok":
        assert r["message"]


def test_2020_has_both_sources():
    assert resolve_year("2020")["periods"][0]["sources"] == ["acs", "census_count"]


def test_2019_acs_only():
    assert resolve_year("2019")["periods"][0]["sources"] == ["acs"]


def test_labels():
    labels = [p["label"] for p in resolve_year("from 2019 to 2020")["periods"]]
    assert labels == ["2015–2019", "2016–2020"]


@pytest.mark.parametrize("text", ["2019 vs 2020", "2020 and 2019"])
def test_comparison_ordered(text):
    r = resolve_year(text)
    assert r["comparison"] is True
    assert [p["year"] for p in r["periods"]] == [2019, 2020]


@pytest.mark.parametrize("text", ["", "most recent", "Today", "NOW"])
def test_latest_words(text):
    assert [p["year"] for p in resolve_year(text)["periods"]] == [2020]


def test_2015_message_explains_range():
    assert "2015–2019" in resolve_year("2015")["message"]


def test_mixed_years():
    assert resolve_year("2018 to 2020")["status"] == "ambiguous"
    assert resolve_year("2010 to 2020")["status"] == "unavailable"


def test_repeated_year_is_single():
    r = resolve_year("2020 and 2020")
    assert r["comparison"] is False and [p["year"] for p in r["periods"]] == [2020]


@pytest.mark.parametrize("text", [None, "latest"])
def test_latest_message_not_current(text):
    m = resolve_year(text)["message"].lower()
    assert m and "2016–2020" in m and "not a current" in m


@pytest.mark.parametrize(
    "text,year",
    [
        ("2015–2019", 2019),
        ("2015-2019", 2019),
        ("2015 to 2019", 2019),
        ("2015 through 2019", 2019),
        ("2016–2020", 2020),
        ("2016-2020", 2020),
        ("2016 to 2020", 2020),
        ("2016 through 2020", 2020),
    ],
)
def test_period_labels(text, year):
    r = resolve_year(text)
    assert r["status"] == "ok" and r["comparison"] is False
    assert [p["year"] for p in r["periods"]] == [year]


def test_label_comparison():
    r = resolve_year("2015–2019 vs 2016–2020")
    assert r["comparison"] is True and [p["year"] for p in r["periods"]] == [2019, 2020]


def test_unrecognized_text_ambiguous():
    assert resolve_year("last year")["status"] == "ambiguous"


def test_2015_and_2020_ambiguous():
    assert resolve_year("2015 and 2020")["status"] == "ambiguous"
