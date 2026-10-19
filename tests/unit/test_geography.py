import inspect
from collections import defaultdict
from pathlib import Path

import pytest

from census_compass import geography_names
from census_compass.config import Settings, get_settings
from census_compass.contracts import load_geography
from census_compass.tools import geography
from census_compass.tools.geography import resolve_geography, resolve_geography_from

FIXTURE_SOURCE = Path(__file__).resolve().parent.parent / "fixtures"
FIXTURE_TOTAL = 17580


def county_filter(code: str) -> str:
    return f"LEFT({{table}}.\"CENSUS_BLOCK_GROUP\", 5) = '{code}'"


def state_filter(code: str) -> str:
    return f"LEFT({{table}}.\"CENSUS_BLOCK_GROUP\", 2) = '{code}'"


@pytest.fixture(autouse=True)
def _fixture_source(monkeypatch):
    monkeypatch.setattr(geography, "get_settings", lambda: Settings(source_dir=FIXTURE_SOURCE))


# --- brief tests -------------------------------------------------------------------


def test_county():
    r = resolve_geography("Cook County, Illinois", 2020)
    assert r["sql_filter"] == "LEFT({table}.\"CENSUS_BLOCK_GROUP\", 5) = '17031'"
    assert r["level"] == "county"
    assert r["status"] == "ok"
    assert r["block_groups"] == 3993
    assert r["name"] == "Cook County, Illinois"
    assert r["year"] == 2020


def test_state():
    r = resolve_geography("Texas", 2020)
    assert r["sql_filter"] == "LEFT({table}.\"CENSUS_BLOCK_GROUP\", 2) = '48'"
    assert (r["status"], r["level"], r["name"]) == ("ok", "state", "Texas")
    assert r["block_groups"] == 68 + 768 + 2588


def test_nation():
    r = resolve_geography("United States", 2020)
    assert r["level"] == "nation" and r["sql_filter"] is None and "Puerto Rico" in r["name"]
    assert r["status"] == "ok"
    assert r["block_groups"] == FIXTURE_TOTAL


def test_ambiguous():
    r = resolve_geography("Orange County", 2020)
    assert r["status"] == "ambiguous" and len(r["candidates"]) >= 2
    assert sorted(r["candidates"]) == sorted(
        ["Orange County, CA", "Orange County, FL", "Orange County, NY", "Orange County, TX"]
    )
    assert r["sql_filter"] is None


def test_case_and_punctuation():
    assert resolve_geography("cook county il", 2020)["status"] == "ok"
    assert resolve_geography("Dona Ana County, NM", 2020)["status"] == "ok"


def test_city_vs_county():
    assert resolve_geography("St. Louis city, MO", 2020)["sql_filter"].endswith("'29510'")


def test_block_group_id_text():
    r = resolve_geography("010010201001", 2020)
    assert r["level"] == "block_group"
    assert r["status"] == "ok"
    assert r["sql_filter"] == "{table}.\"CENSUS_BLOCK_GROUP\" = '010010201001'"
    assert r["block_groups"] == 1


def test_unknown_id():
    assert resolve_geography("999999999999", 2020)["status"] == "not_found"


def test_no_data():
    assert resolve_geography("Guam", 2020)["status"] == "no_data"
    assert resolve_geography("St. Croix Island, VI", 2020)["status"] == "no_data"


def test_unsupported():
    assert resolve_geography("Austin", 2020)["status"] == "unsupported"
    assert resolve_geography("ZIP 10001", 2020)["status"] == "unsupported"


# --- signature and loading -----------------------------------------------------------


def test_tool_signature_is_place_and_year():
    assert list(inspect.signature(resolve_geography).parameters) == ["place", "year"]
    assert "12" in resolve_geography.__doc__ and "COUNTY_FIPS" in resolve_geography.__doc__


def test_explicit_source_dir():
    r = resolve_geography_from("Cook County, IL", 2020, source_dir=FIXTURE_SOURCE)
    assert r["sql_filter"] == county_filter("17031")


def test_year_without_lookup_is_not_found():
    r = resolve_geography("Texas", 2015)
    assert r["status"] == "not_found"
    assert "resolve_year" in r["message"]


def test_missing_geography_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        resolve_geography_from("Texas", 2019, source_dir=tmp_path)


# --- states and nation ---------------------------------------------------------------


@pytest.mark.parametrize(
    "place,fips",
    [
        ("texas", "48"),
        ("TX", "48"),
        ("Illinois", "17"),
        ("il", "17"),
        ("Puerto Rico", "72"),
        ("New York", "36"),
        ("Washington, DC", "11"),
        ("Washington D.C.", "11"),
        ("District of Columbia", "11"),
        ("DC", "11"),
        ("the state of Texas", "48"),
        ("Texas state", "48"),
    ],
)
def test_state_wordings(place, fips):
    r = resolve_geography(place, 2020)
    assert (r["status"], r["level"], r["sql_filter"]) == ("ok", "state", state_filter(fips))


def test_state_without_rows_in_lookup_is_no_data():
    # The fixture has no Wyoming counties.
    assert resolve_geography("Wyoming", 2020)["status"] == "no_data"


@pytest.mark.parametrize("place", ["American Samoa", "Guam", "U.S. Virgin Islands", "GU", "MP"])
def test_territories_are_no_data(place):
    r = resolve_geography(place, 2020)
    assert r["status"] == "no_data"
    assert r["sql_filter"] is None


@pytest.mark.parametrize(
    "place", ["US", "U.S.", "USA", "the United States", "national", "nationwide", "America"]
)
def test_nation_wordings(place):
    r = resolve_geography(place, 2020)
    assert (r["status"], r["level"], r["sql_filter"]) == ("ok", "nation", None)
    assert r["name"] == "United States (including Puerto Rico)"


def test_all_52_states_in_names_module():
    assert len(geography_names.STATES) == 52
    assert len({s.fips for s in geography_names.STATES}) == 52
    assert {s.abbr for s in geography_names.TERRITORIES} == {"AS", "GU", "MP", "VI"}


# --- counties ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "place,code",
    [
        ("Cook County, Illinois", "17031"),
        ("cook county il", "17031"),
        ("COOK COUNTY, IL", "17031"),
        ("Cook County IL USA", "17031"),
        ("Doña Ana County, New Mexico", "35013"),
        ("Dona Ana County NM", "35013"),
        ("Doña Ana County", "35013"),
        ("St. Louis city, MO", "29510"),
        ("Saint Louis City, Missouri", "29510"),
        ("St Louis city", "29510"),
        ("St. Louis County, MO", "29189"),
        ("Saint Louis County, Missouri", "29189"),
        ("Baltimore city", "24510"),
        ("Baltimore County, MD", "24005"),
        ("Orleans Parish", "22071"),
        ("Orleans Parish, Louisiana", "22071"),
        ("Anchorage Municipality", "02020"),
        ("Juneau City and Borough", "02110"),
        ("Juneau Borough, AK", "02110"),
        ("Juneau County", "55057"),
        ("Yukon-Koyukuk Census Area", "02290"),
        ("Yukon Koyukuk census area, alaska", "02290"),
        ("San Juan Municipio, PR", "72127"),
        ("San Juan Municipio", "72127"),
        ("Carson City", "32510"),
        ("Carson City, NV", "32510"),
        ("Orange County, CA", "06059"),
        ("Orange County, California", "06059"),
        ("orange county tx", "48361"),
        ("Orange County, New York", "36071"),
        ("DuPage County", "17043"),
        ("Du Page County, IL", "17043"),
        ("County of Cook, IL", "17031"),
        ("New York County", "36061"),
        ("Bronx County", "36005"),
    ],
)
def test_county_wordings(place, code):
    r = resolve_geography(place, 2020)
    assert (r["status"], r["level"]) == ("ok", "county"), r
    assert r["sql_filter"] == county_filter(code)


def test_st_louis_county_without_state_is_ambiguous():
    r = resolve_geography("St. Louis County", 2020)
    assert r["status"] == "ambiguous"
    assert sorted(r["candidates"]) == ["St. Louis County, MN", "St. Louis County, MO"]


@pytest.mark.parametrize("place", ["St. Louis, MO", "St. Louis"])
def test_st_louis_bare_lists_city_and_county(place):
    r = resolve_geography(place, 2020)
    assert r["status"] == "ambiguous"
    assert "St. Louis city, MO" in r["candidates"]
    assert "St. Louis County, MO" in r["candidates"]
    assert r["sql_filter"] is None


@pytest.mark.parametrize(
    "place,candidate",
    [
        ("Cook", "Cook County, IL"),
        ("Cook, IL", "Cook County, IL"),
        ("Travis, TX", "Travis County, TX"),
    ],
)
def test_bare_name_without_county_word_needs_confirmation(place, candidate):
    # A bare word may be a city sharing a county's name; never resolve it silently.
    r = resolve_geography(place, 2020)
    assert r["status"] == "ambiguous"
    assert r["candidates"] == [candidate]
    assert r["sql_filter"] is None


def test_county_in_wrong_state_is_not_found_with_other_states():
    r = resolve_geography("Cook County, Texas", 2020)
    assert r["status"] == "not_found"
    assert "Cook County, IL" in r["candidates"]
    assert len(r["candidates"]) <= 3


def test_misspelled_county_suggests_close_names():
    r = resolve_geography("Cok County, Illinois", 2020)
    assert r["status"] == "not_found"
    assert "Cook County, IL" in r["candidates"]
    assert len(r["candidates"]) <= 3


def test_unknown_county_suggests_at_most_three():
    r = resolve_geography("Orangeville County", 2020)
    assert r["status"] == "not_found"
    assert 1 <= len(r["candidates"]) <= 3


def test_close_typo_without_county_word_is_not_found():
    r = resolve_geography("Travis Cuonty", 2020)
    assert r["status"] == "not_found"
    assert "Travis County, TX" in r["candidates"]


# --- unsupported ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "place",
    [
        "Austin",
        "Austin, TX",
        "Brooklyn",
        "Brooklyn, New York",
        "New York City",
        "Salt Lake City",
        "city of Chicago",
        "town of Greenwich",
        "the Mission neighborhood",
        "10001",
        "10001-1234",
        "zip 10001",
        "ZIP code 60614",
        "Hyde Park neighbourhood, Chicago",
    ],
)
def test_unsupported_places(place):
    r = resolve_geography(place, 2020)
    assert r["status"] == "unsupported", r
    assert r["sql_filter"] is None
    assert "state" in r["message"] and "count" in r["message"] and "block group" in r["message"]


def test_city_of_independent_city_offers_it():
    r = resolve_geography("city of Baltimore", 2020)
    assert r["status"] == "unsupported"
    assert r["candidates"] == ["Baltimore city, MD"]


# --- block groups --------------------------------------------------------------------


def test_block_group_with_label():
    r = resolve_geography("block group 170310101002", 2020)
    assert (r["status"], r["level"]) == ("ok", "block_group")
    assert r["sql_filter"] == "{table}.\"CENSUS_BLOCK_GROUP\" = '170310101002'"
    assert "Cook County, Illinois" in r["name"]


def test_block_group_in_county_without_data_is_no_data():
    assert resolve_geography("660109501001", 2020)["status"] == "no_data"


@pytest.mark.parametrize("place", ["1703101010", "17031010100299", "17031010100"])
def test_wrong_length_ids_are_not_found(place):
    r = resolve_geography(place, 2020)
    assert r["status"] == "not_found"
    assert "12" in r["message"]


def test_empty_place_is_not_found():
    assert resolve_geography("  ", 2020)["status"] == "not_found"


# --- real data (runs only where the generated geography files exist) -----------------

_REAL_FACTS = {2020: (3234, 242335), 2019: (3233, 220333)}


def _real(year):
    source = get_settings().source_dir
    if not (Path(source) / "geography" / f"{year}.json").is_file():
        pytest.skip(f"{source}/geography/{year}.json not present")
    return source, load_geography(source, year)


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_counts(year):
    source, records = _real(year)
    count, total = _REAL_FACTS[year]
    assert len(records) == count
    r = resolve_geography_from("United States", year, source_dir=source)
    assert r["block_groups"] == total == sum(c.block_groups for c in records)
    zero = [c for c in records if c.block_groups == 0]
    assert {c.state_abbr for c in zero} <= {"AS", "GU", "MP", "VI"}


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_every_county_resolves_to_itself(year):
    source, records = _real(year)
    for c in records:
        code = c.state_fips + c.county_fips
        for place in (
            f"{c.county}, {c.state_abbr}",
            f"{c.county}, {geography_names.state_by_abbr(c.state_abbr).name}",
        ):
            r = resolve_geography_from(place, year, source_dir=source)
            if c.block_groups == 0:
                assert r["status"] == "no_data", (place, r)
            else:
                assert (r["status"], r["sql_filter"]) == ("ok", county_filter(code)), (place, r)
                assert r["block_groups"] == c.block_groups


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_shared_names_are_ambiguous_with_all(year):
    source, records = _real(year)
    by_name = defaultdict(list)
    for c in records:
        by_name[c.county].append(f"{c.county}, {c.state_abbr}")
    shared = {name: cands for name, cands in by_name.items() if len(cands) > 1}
    assert shared
    for name, cands in shared.items():
        r = resolve_geography_from(name, year, source_dir=source)
        assert r["status"] == "ambiguous", (name, r)
        assert sorted(r["candidates"]) == sorted(cands)


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_zero_block_group_counties_are_no_data(year):
    source, records = _real(year)
    zero = [c for c in records if c.block_groups == 0]
    assert len(zero) == 13
    for c in zero:
        r = resolve_geography_from(f"{c.county}, {c.state_abbr}", year, source_dir=source)
        assert r["status"] == "no_data"


@pytest.mark.parametrize("year", [2019, 2020])
def test_real_states_sum_their_counties(year):
    source, records = _real(year)
    for s in geography_names.STATES:
        r = resolve_geography_from(s.name, year, source_dir=source)
        assert (r["status"], r["sql_filter"]) == ("ok", state_filter(s.fips))
        assert r["block_groups"] == sum(c.block_groups for c in records if c.state_fips == s.fips)
