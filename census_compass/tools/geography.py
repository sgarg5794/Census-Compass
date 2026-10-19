"""Geography resolution tool: maps a place the user named to a SQL filter on block group IDs."""

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from census_compass.config import get_settings
from census_compass.contracts import CountyRecord, GeographyResolution, load_geography
from census_compass.geography_names import STATE_ALIASES, STATES, TERRITORIES, StateName

_YEARS = (2019, 2020)
_NATION_NAME = "United States (including Puerto Rico)"
_STATE_FILTER = "LEFT({{table}}.\"CENSUS_BLOCK_GROUP\", 2) = '{}'"
_COUNTY_FILTER = "LEFT({{table}}.\"CENSUS_BLOCK_GROUP\", 5) = '{}'"
_BLOCK_GROUP_FILTER = "{{table}}.\"CENSUS_BLOCK_GROUP\" = '{}'"

_SUPPORTED = (
    "Only states, counties (and county equivalents such as parishes, boroughs and "
    "independent cities) and census block groups are supported; city, town, ZIP code "
    "and neighborhood boundaries are not in the data."
)

# County-type words, longest first. "city" alone marks an independent city.
_TYPE_WORDS = (
    "city and borough",
    "census area",
    "municipality",
    "municipio",
    "borough",
    "parish",
    "county",
    "city",
)
_NATION_WORDS = {
    "us",
    "usa",
    "united states",
    "united states of america",
    "america",
    "nation",
    "national",
    "nationwide",
    "nationally",
    "country",
    "whole country",
    "entire country",
    "all states",
}
_TRAILING_NATION = ("united states of america", "united states", "usa", "us")
_EXPLICIT_UNSUPPORTED_RE = re.compile(
    r"\b(zip|zipcode|zip code|postal code|neighborhood|neighbourhood|"
    r"(?:city|town|village|township) of)\b"
)
_CITY_OF_RE = re.compile(r"^(?:the )?city of (.+)$")
_COUNTY_OF_RE = re.compile(r"^(?:the )?county of (.+)$")
_BG_LABEL_RE = re.compile(
    r"^(?:census )?(?:block group|blockgroup|cbg|bg|geoid)(?: id| number| no)?\s*"
)
_ZIP_RE = re.compile(r"^\d{5}(?:-\d{4})?$")
_WORD_FORMS = {"st": "saint", "ste": "sainte", "cnty": "county", "cty": "county"}
# "Co." (with a period) or "Co," after a name is always the word county, never Colorado.
_CO_PERIOD_RE = re.compile(r"(?i)\b(?:co|cnty|cty)\.")
_CO_COMMA_RE = re.compile(r"(?i)(?<=[a-z])\s+co\s*,")
_LA_COUNTY_RE = re.compile(r"^la county\b")
_COMMA_CO_RE = re.compile(r"(?i),\s*co\s*$")  # "Denver, CO": the state only


def _normalize(text: str) -> str:
    """Lowercase, drop diacritics and punctuation, and spell out St./Ste."""
    s = unicodedata.normalize("NFKD", text)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[.'’`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [_WORD_FORMS.get(w, w) for w in s.split()]
    return " ".join(words)


def _expand_county_abbreviations(raw: str) -> str:
    """Spell out "Co." / "Cnty." and "Co," ("Cook Co, IL"); a bare "co" stays ambiguous."""
    s = _CO_PERIOD_RE.sub(" county ", raw)
    return _CO_COMMA_RE.sub(" county,", s)


def _split_type(key: str) -> tuple[str | None, str]:
    """Split a normalized name into (county-type word, base name)."""
    for word in _TYPE_WORDS:
        if key.endswith(" " + word):
            return word, key[: -len(word) - 1]
    return None, key


def _nospace(key: str) -> str:
    return key.replace(" ", "")


def _display(c: CountyRecord) -> str:
    return f"{c.county}, {c.state_abbr}"


def _state_lookup() -> dict[str, StateName]:
    lookup: dict[str, StateName] = {}
    for s in (*STATES, *TERRITORIES):
        for name in (s.name, s.abbr, *STATE_ALIASES.get(s.abbr, ())):
            lookup[_normalize(name)] = s
    return lookup


_STATE_LOOKUP = _state_lookup()
_TERRITORY_ABBRS = {s.abbr for s in TERRITORIES}
_STATE_NAME_BY_ABBR = {s.abbr: s.name for s in (*STATES, *TERRITORIES)}


def _long_name(c: CountyRecord) -> str:
    return f"{c.county}, {_STATE_NAME_BY_ABBR.get(c.state_abbr, c.state_abbr)}"


@dataclass
class _Entry:
    record: CountyRecord
    full: str
    base: str
    kind: str | None


@dataclass
class _Index:
    by_code: dict[str, CountyRecord] = field(default_factory=dict)
    entries: list[_Entry] = field(default_factory=list)
    state_totals: dict[str, int] = field(default_factory=dict)
    total: int = 0


@lru_cache(maxsize=8)
def _load_index(source_dir: Path, year: int) -> _Index:
    index = _Index()
    for c in load_geography(source_dir, year):
        full = _normalize(c.county)
        kind, base = _split_type(full)
        index.by_code[c.state_fips + c.county_fips] = c
        index.entries.append(_Entry(c, full, base, kind))
        index.state_totals[c.state_fips] = index.state_totals.get(c.state_fips, 0) + c.block_groups
        index.total += c.block_groups
    return index


def _result(year: int, **kwargs) -> dict:
    return GeographyResolution(year=year, **kwargs).model_dump()


def _unsupported(year: int, place: str, candidates: list[str] | None = None) -> dict:
    extra = f" Did the user mean {' or '.join(candidates)}?" if candidates else ""
    return _result(
        year,
        status="unsupported",
        candidates=candidates or [],
        message=f"'{place}' is not a state, county or census block group. {_SUPPORTED}{extra}",
    )


def _state_result(year: int, s: StateName, index: _Index) -> dict:
    total = index.state_totals.get(s.fips, 0)
    if s.abbr in _TERRITORY_ABBRS or total == 0:
        return _result(
            year,
            status="no_data",
            name=s.name,
            message=f"{s.name} has no demographic data in this dataset for {year}. "
            "Only the 50 states, the District of Columbia and Puerto Rico have data.",
        )
    return _result(
        year,
        status="ok",
        name=s.name,
        level="state",
        sql_filter=_STATE_FILTER.format(s.fips),
        block_groups=total,
    )


def _county_result(year: int, c: CountyRecord) -> dict:
    name = _long_name(c)
    if c.block_groups == 0:
        return _result(
            year,
            status="no_data",
            name=name,
            message=f"{name} is in the {year} county lookup but has no demographic data.",
        )
    return _result(
        year,
        status="ok",
        name=name,
        level="county",
        sql_filter=_COUNTY_FILTER.format(c.state_fips + c.county_fips),
        block_groups=c.block_groups,
    )


def _match(index: _Index, query: str, state: str | None) -> tuple[list[CountyRecord], bool]:
    """Return (matching counties, matched only by a bare base name without a type word).

    Tiers, first hit wins: full name; full name ignoring spaces; base name (the name
    without its county-type word) of a compatible type; base name ignoring spaces.
    The full name is tried first so that "St. Louis city" never matches the county.
    """
    entries = [e for e in index.entries if state is None or e.record.state_abbr == state]
    hits = [e for e in entries if e.full == query]
    if not hits:
        hits = [e for e in entries if _nospace(e.full) == _nospace(query)]
    if hits:
        return [e.record for e in hits], False

    kind, base = _split_type(query)
    if kind is None:
        compatible = entries
    elif kind == "city":
        compatible = [e for e in entries if e.kind == "city"]
    else:
        compatible = [e for e in entries if e.kind != "city"]
    hits = [e for e in compatible if e.base == base]
    if not hits:
        hits = [e for e in compatible if _nospace(e.base) == _nospace(base)]
    return [e.record for e in hits], kind is None and bool(hits)


def _suggest(index: _Index, query: str, state: str | None, cutoff: float = 0.6) -> list[str]:
    """Up to 3 closest county names (difflib), within the state when one was given."""
    keyed: dict[str, dict[int, CountyRecord]] = {}
    for e in index.entries:
        if state is None or e.record.state_abbr == state:
            keyed.setdefault(e.full, {})[id(e.record)] = e.record
            keyed.setdefault(e.base, {})[id(e.record)] = e.record
    out: list[str] = []
    for key in difflib.get_close_matches(query, list(keyed), n=6, cutoff=cutoff):
        records = list(keyed[key].values())
        if len(records) > 1 and len({r.county for r in records}) == 1:
            labels = [records[0].county]  # Shared name: resolving it lists every state.
        else:
            labels = [_display(r) for r in records]
        out.extend(label for label in labels if label not in out)
    return out[:3]


def _parse_state_tail(query: str) -> list[tuple[str, StateName]]:
    """Possible (head, state) splits where the trailing words name a state, longest first."""
    words = query.split()
    splits = []
    for n in range(min(4, len(words) - 1), 0, -1):
        s = _STATE_LOOKUP.get(" ".join(words[-n:]))
        if s:
            splits.append((" ".join(words[:-n]), s))
    return splits


def _as_state(query: str) -> StateName | None:
    stripped = re.sub(r" state$", "", re.sub(r"^(?:the )?state of ", "", query))
    return _STATE_LOOKUP.get(query) or _STATE_LOOKUP.get(stripped)


def _co_to_county(head: str) -> str:
    return head[:-3] + " county" if head.endswith(" co") else head


def _attempts(query: str) -> list[tuple[str, str | None]]:
    """(name, state abbreviation) readings of a query: with a trailing state, then without.

    Before a state, a trailing "co" is the word county ("cook co il").
    """
    splits = [(_co_to_county(head), s.abbr) for head, s in _parse_state_tail(query)]
    return splits + [(query, None)]


def _find(index: _Index, query: str) -> tuple[list[CountyRecord], bool, str, str | None]:
    """First reading of the query that matches: (records, bare, name, state abbreviation)."""
    attempts = _attempts(query)
    for head, state in attempts:
        records, bare = _match(index, head, state)
        if records:
            return records, bare, head, state
    head, state = attempts[0]
    return [], False, head, state


def _resolve_block_group(year: int, place: str, digits: str, index: _Index) -> dict:
    if len(digits) != 12:
        return _result(
            year,
            status="not_found",
            message=f"'{place}' is not a census block group ID. A block group ID has exactly "
            "12 digits: state (2) + county (3) + tract (6) + block group (1).",
        )
    county = index.by_code.get(digits[:5])
    if county is None:
        return _result(
            year,
            status="not_found",
            message=f"Block group {digits}: county {digits[:5]} is not in the {year} lookup.",
        )
    name = f"Block group {digits} ({_long_name(county)})"
    if county.block_groups == 0:
        return _result(
            year,
            status="no_data",
            name=name,
            message=f"{_long_name(county)} has no demographic data in {year}.",
        )
    return _result(
        year,
        status="ok",
        name=name,
        level="block_group",
        sql_filter=_BLOCK_GROUP_FILTER.format(digits),
        block_groups=1,
        message="The block group's county exists, but the ID itself cannot be checked in "
        "advance; if the query returns no rows, this block group does not exist.",
    )


def _resolve_county(year: int, place: str, query: str, index: _Index) -> dict:
    comma_co = bool(_COMMA_CO_RE.search(place))
    county_of = _COUNTY_OF_RE.match(query)
    if county_of:  # "county of Cook, IL" -> "cook county il"
        rest = county_of.group(1)
        splits = _parse_state_tail(rest)
        query = f"{splits[0][0]} county {splits[0][1].abbr.lower()}" if splits else f"{rest} county"

    query = _LA_COUNTY_RE.sub("los angeles county", query)  # "LA County", "L.A. County"

    # A bare trailing "co" is either the word county or Colorado: try both, merge.
    both = query.endswith(" co") and not comma_co
    readings = [_co_to_county(query), query] if both else [query]
    found = [_find(index, q) for q in readings]
    records = list({id(r): r for f in found for r in f[0]}.values())
    bare = all(f[1] for f in found if f[0])
    _, _, head, state = found[0]

    if records and bare:
        candidates = [_display(r) for r in records]
        return _result(
            year,
            status="ambiguous",
            candidates=candidates,
            message=f"'{place}' has no county word; it may be a city or town, which is not "
            f"supported. Ask the user whether they mean {' or '.join(candidates)}.",
        )
    if len(records) > 1:
        return _result(
            year,
            status="ambiguous",
            candidates=[_display(r) for r in records],
            message=f"'{place}' matches several counties. Ask the user which one they mean.",
        )
    if records:
        return _county_result(year, records[0])

    kind, _ = _split_type(head)
    if kind == "city":
        return _unsupported(year, place)
    if kind is None and not _suggest(index, head, None, cutoff=0.85):
        return _unsupported(year, place)

    candidates = [_display(r) for r in _match(index, head, None)[0]] if state else []
    candidates += [c for c in _suggest(index, head, state) if c not in candidates]
    where = f" in {_STATE_NAME_BY_ABBR[state]}" if state else ""
    city_hint = f" It may be a city or town. {_SUPPORTED}" if kind is None else ""
    return _result(
        year,
        status="not_found",
        candidates=candidates[:3],
        message=f"No county matching '{place}' was found{where} in the {year} lookup."
        + city_hint
        + (" The closest county names are listed as candidates." if candidates else ""),
    )


def _resolve_unsupported_wording(year: int, place: str, query: str, index: _Index) -> dict:
    """ "city of X" may still name an independent city ("city of Baltimore"): offer it."""
    candidates: list[str] = []
    city_of = _CITY_OF_RE.match(query)
    if city_of:
        for head, state in _attempts(city_of.group(1)):
            records, _ = _match(index, f"{head} city", state)
            candidates = [_display(r) for r in records]
            if candidates:
                break
    return _unsupported(year, place, candidates)


def resolve_geography_from(place: str, year: int, *, source_dir: Path | None = None) -> dict:
    """resolve_geography with an explicit geography source directory.

    `source_dir` holds geography/{year}.json; it defaults to the configured source_dir.
    """
    year = int(year)
    raw = (place or "").strip()
    if year not in _YEARS:
        return _result(
            year,
            status="not_found",
            message="Geography is available only for 2019 and 2020. Call resolve_year first "
            "and pass the year of the resolved period.",
        )
    if not raw:
        return _result(year, status="not_found", message="No place was given.")

    source = Path(source_dir) if source_dir is not None else Path(get_settings().source_dir)
    index = _load_index(source, year)
    query = _normalize(_expand_county_abbreviations(raw))

    # Numbers: ZIP codes, and block group IDs with an optional "block group" label.
    if _ZIP_RE.match(re.sub(r"\s+", "", raw)):
        return _unsupported(year, raw)
    digits = _BG_LABEL_RE.sub("", query).replace(" ", "")
    if digits.isdigit():
        return _resolve_block_group(year, raw, digits, index)

    if _EXPLICIT_UNSUPPORTED_RE.search(query):
        return _resolve_unsupported_wording(year, raw, query, index)

    query = re.sub(r"^the ", "", query)
    for suffix in _TRAILING_NATION:
        if query.endswith(" " + suffix):  # "Cook County, IL, USA"
            query = query[: -len(suffix) - 1]
            break
    if query in _NATION_WORDS:
        return _result(
            year,
            status="ok",
            name=_NATION_NAME,
            level="nation",
            block_groups=index.total,
            message="National figures include Puerto Rico.",
        )
    if query == "la":  # "LA" / "L.A.": Louisiana or Los Angeles (controller ruling)
        candidates = ["Louisiana"] + [
            _display(r) for r in _match(index, "los angeles county", "CA")[0]
        ]
        return _result(
            year,
            status="ambiguous",
            candidates=candidates,
            message="'LA' may mean Louisiana or Los Angeles County, CA. Ask the user which.",
        )
    state = _as_state(query)
    if state:
        return _state_result(year, state, index)
    return _resolve_county(year, raw, query, index)


def resolve_geography(place: str, year: int) -> dict:
    """Resolve a place the user named into a SQL filter for that year's block groups.

    Call this once per resolved period (after resolve_year): for a 2019 vs 2020
    comparison, call it for 2019 and again for 2020, since the county lookups differ
    between years. A place valid in one year but "not_found" or "no_data" in the other
    is reported for that year only.

    Every demographic row is one census block group, keyed by "CENSUS_BLOCK_GROUP": a
    12-digit text ID = state FIPS (2) + county FIPS (3) + tract (6) + block group (1),
    for example '170310101002' (leading zeros are kept; always compare it as text).
    The returned "sql_filter" contains the literal placeholder {table}: replace it with
    the quoted table name (or alias) and paste it into the WHERE clause:
    - state:  LEFT({table}."CENSUS_BLOCK_GROUP", 2) = '48'
    - county: LEFT({table}."CENSUS_BLOCK_GROUP", 5) = '17031'
    - block group: {table}."CENSUS_BLOCK_GROUP" = '170310101002'
    - nation: no filter (sql_filter is null); always labelled
      "United States (including Puerto Rico)".
    To show county names, join the same year's county lookup (many-to-one):
    LEFT("<A>"."CENSUS_BLOCK_GROUP", 5) =
    "<Y>_METADATA_CBG_FIPS_CODES"."STATE_FIPS" || "<Y>_METADATA_CBG_FIPS_CODES"."COUNTY_FIPS"

    Rules:
    - A state name or USPS abbreviation ("Texas", "TX") is a state; "Washington, DC"
      and "District of Columbia" are state 11. A bare state name always means the
      state ("Washington" is the state; say "Washington County" for counties).
      Only the 50 states, DC and Puerto Rico have data; American Samoa, Guam, the
      Northern Mariana Islands and the U.S. Virgin Islands are "no_data".
    - Counties and county equivalents (parish, borough, census area, municipio,
      municipality, city and borough, independent city) match ignoring case,
      punctuation, accents ("Dona Ana" = "Doña Ana") and "St."/"Saint". Add the state,
      with or without a comma: "Cook County, Illinois", "cook county il".
      "Co.", "Cnty" and "Cty" mean County ("Cook Co, IL"); a bare trailing "Co" may
      mean County or Colorado, so both readings are offered ("El Paso Co").
      "LA" alone is "ambiguous" (Louisiana or Los Angeles County, CA);
      "LA County" is Los Angeles County.
      "St. Louis city, MO" is the independent city (29510), "St. Louis County, MO" the
      county (29189).
    - A county name found in several states, without a state ("Orange County"), is
      "ambiguous": the candidates list every match ("Orange County, CA", ...). Ask the
      user which one they mean, then call again with the chosen candidate.
    - A name without a county word that only matches a county's base name ("Cook, IL",
      "Austin, TX") is "ambiguous" even with one candidate: it may be a city sharing a
      county's name. Confirm the county with the user; never assume city = county.
    - A county in the lookup without demographic rows is "no_data".
    - An unknown or misspelled county is "not_found" with up to 3 closest names as
      candidates; offer them to the user.
    - Cities, towns, ZIP codes and neighborhoods are "unsupported" (their boundaries
      are not in the data): ZIP codes (5 digits or "zip ..."), "city of ...",
      "town of ...", "... neighborhood", "... City" that is not an independent city,
      and any name without a county word that matches no state or county and is not
      at least 85% similar to a county name ("Austin", "Brooklyn"). Explain this to the
      user and offer the county or state instead. A name without a county word that
      is close to a county name ("Chicago" ~ "Chisago County") is "not_found"; its
      message says it may be a city, which is not supported.
    - A 12-digit ID is a block group: "ok" when its first 5 digits are a county with
      data that year, "no_data" when that county has none, "not_found" when the county
      is not in the lookup. Whether the individual ID exists cannot be checked in
      advance; if the query returns no rows, it does not exist. Numbers of any other
      length are "not_found".
    - A year other than 2019 or 2020 is "not_found": call resolve_year first.

    Args:
        place: The place as the user wrote it, such as "Cook County, IL", "Texas",
            "United States" or "170310101002".
        year: The resolved period year from resolve_year: 2019 or 2020.

    Returns:
        A dict with "status" ("ok", "ambiguous", "unsupported", "not_found" or
        "no_data"), "year", "name" (label to show the user), "level" ("nation",
        "state", "county" or "block_group"), "sql_filter" (set only when status is
        "ok"; null for the nation), "block_groups" (how many block groups the place
        covers), "candidates" (choices or suggestions to offer the user) and "message"
        (a short sentence to relay, always when the status is not ok).
    """
    return resolve_geography_from(place, year)
