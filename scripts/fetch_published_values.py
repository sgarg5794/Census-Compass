"""Fetch published ACS 5-year values for the full-coverage hand-written measures.

For every full-coverage measure (spec section 6.1; plan Task 5 table, all except the race-specific
mean household income measures) this script reads the Census Bureau's published value for the
United States (`us:1`, which excludes Puerto Rico) and California (`state:06`) for the 2019
(2015-2019) and 2020 (2016-2020) ACS 5-year releases, and writes
`tests/fixtures/published/{year}.json`.

Variables are found by label in each endpoint's `variables.json` (variable numbers differ between
years); numbers are never hard-coded. A label pattern must match exactly one estimate variable of
the named table group, otherwise the script stops. Measures with no single published variable
are derived from published estimates of the same release (`derived: true`, with every component
recorded). API responses are treated as data only.

Values: with a `CENSUS_API_KEY` environment variable the Census data API is queried (it now
refuses keyless data queries); without one the same published tables are read from the
data.census.gov table service (`ACSDP5Y`, `ACSST5Y`, `ACSDT5Y` table ids), which needs no key.
Labels always come from the Census data API `variables.json`, which needs no key.

Usage: python scripts/fetch_published_values.py [--years 2019 2020] [--out DIR]
"""

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

API = "https://api.census.gov/data/{year}/acs/acs5{path}"
ENDPOINTS = {"profile": "/profile", "subject": "/subject", "detailed": ""}
GEOGRAPHIES = {"us_excluding_pr": "us:1", "state:06": "state:06"}
DATA_CENSUS = "https://data.census.gov/api/access/data/table?{query}"
TABLE_PREFIX = {"profile": "ACSDP5Y", "subject": "ACSST5Y", "detailed": "ACSDT5Y"}
GEO_IDS = {"us:1": "0100000US", "state:06": "0400000US06"}
YEARS = (2019, 2020)
OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "published"
TIMEOUT = 120

INCOME = r"Estimate!!INCOME AND BENEFITS \(IN {year} INFLATION-ADJUSTED DOLLARS\)"
GRAPI = (r"Estimate!!GROSS RENT AS A PERCENTAGE OF HOUSEHOLD INCOME \(GRAPI\)!!Occupied units "
         r"paying rent \(excluding units where GRAPI cannot be computed\)")
OCCUPANTS = r"Estimate!!OCCUPANTS PER ROOM!!Occupied housing units"
AGG_EARNINGS = r"Estimate!!Aggregate earnings in the past 12 months \(in {year} inflation-adjusted dollars\):"


@dataclass(frozen=True)
class Direct:
    """The measure is one published variable."""

    endpoint: str
    group: str
    label: str


@dataclass(frozen=True)
class Derived:
    """scale * sum(numerator) / (sum(denominator) - sum(subtract)), all published estimates."""

    endpoint: str
    group: str
    numerator: tuple[str, ...]
    denominator: tuple[str, ...]
    scale: float
    reason: str
    denominator_group: str | None = None
    subtract: tuple[str, ...] = field(default=())


SPECS: dict[str, Direct | Derived] = {
    "sex_ratio": Direct("profile", "DP05",
                        r"Estimate!!SEX AND AGE!!Total population!!Sex ratio \(males per 100 females\)"),
    "age_dependency_ratio": Direct(
        "subject", "S0101", r"Estimate!!Total!!Total population!!SUMMARY INDICATORS!!Age dependency ratio"),
    "child_dependency_ratio": Direct(
        "subject", "S0101", r"Estimate!!Total!!Total population!!SUMMARY INDICATORS!!Child dependency ratio"),
    "old_age_dependency_ratio": Direct(
        "subject", "S0101",
        r"Estimate!!Total!!Total population!!SUMMARY INDICATORS!!Old-age dependency ratio"),
    "mean_travel_time_to_work": Direct(
        "profile", "DP03",
        r"Estimate!!COMMUTING TO WORK!!Workers 16 years and over!!Mean travel time to work \(minutes\)"),
    "high_school_or_higher_pct": Direct(
        "profile", "DP02",
        r"Percent!!EDUCATIONAL ATTAINMENT!!Population 25 years and over!!High school graduate or higher"),
    "bachelors_or_higher_pct": Direct(
        "profile", "DP02",
        r"Percent!!EDUCATIONAL ATTAINMENT!!Population 25 years and over!!Bachelor's degree or higher"),
    "poverty_rate": Direct(
        "subject", "S1701",
        r"Estimate!!Percent below poverty level!!Population for whom poverty status is determined"),
    "family_poverty_rate": Direct(
        "profile", "DP03",
        r"Percent!!PERCENTAGE OF FAMILIES AND PEOPLE WHOSE INCOME IN THE PAST 12 MONTHS IS BELOW "
        r"THE POVERTY LEVEL!!All families"),
    "mean_household_income": Direct(
        "profile", "DP03", INCOME + r"!!Total households!!Mean household income \(dollars\)"),
    "mean_family_income": Direct(
        "profile", "DP03", INCOME + r"!!Families!!Mean family income \(dollars\)"),
    "per_capita_income": Direct("profile", "DP03", INCOME + r"!!Per capita income \(dollars\)"),
    "mean_household_earnings": Direct(
        "profile", "DP03", INCOME + r"!!Total households!!With earnings!!Mean earnings \(dollars\)"),
    "mean_earnings": Derived(
        "detailed", "B20003", (AGG_EARNINGS,), (r"Estimate!!Total:",), 1,
        "No profile or subject table publishes mean earnings for all people 16+ with earnings "
        "(S2001 has it only for full-time, year-round workers); derived from the published "
        "aggregate earnings and earner count.", denominator_group="B20001"),
    "mean_earnings_male": Derived(
        "detailed", "B20003", (AGG_EARNINGS + r"!!Male:",), (r"Estimate!!Total:!!Male:",), 1,
        "No profile or subject table publishes mean earnings for all male earners 16+; derived "
        "from the published aggregate earnings and earner count.", denominator_group="B20001"),
    "mean_earnings_female": Derived(
        "detailed", "B20003", (AGG_EARNINGS + r"!!Female:",), (r"Estimate!!Total:!!Female:",), 1,
        "No profile or subject table publishes mean earnings for all female earners 16+; derived "
        "from the published aggregate earnings and earner count.", denominator_group="B20001"),
    "unemployment_rate": Direct(
        "profile", "DP03", r"Percent!!EMPLOYMENT STATUS!!Civilian labor force!!Unemployment Rate"),
    "labor_force_participation_rate": Direct(
        "profile", "DP03", r"Percent!!EMPLOYMENT STATUS!!Population 16 years and over!!In labor force"),
    "homeowner_vacancy_rate": Direct(
        "profile", "DP04", r"Estimate!!HOUSING OCCUPANCY!!Total housing units!!Homeowner vacancy rate"),
    "rental_vacancy_rate": Direct(
        "profile", "DP04", r"Estimate!!HOUSING OCCUPANCY!!Total housing units!!Rental vacancy rate"),
    "average_household_size": Direct(
        "profile", "DP02", r"Estimate!!HOUSEHOLDS BY TYPE!!Total households!!Average household size"),
    "average_household_size_owner": Direct(
        "profile", "DP04",
        r"Estimate!!HOUSING TENURE!!Occupied housing units!!Average household size of owner-occupied unit"),
    "average_household_size_renter": Direct(
        "profile", "DP04",
        r"Estimate!!HOUSING TENURE!!Occupied housing units!!Average household size of renter-occupied unit"),
    "homeownership_rate": Direct(
        "profile", "DP04", r"Percent!!HOUSING TENURE!!Occupied housing units!!Owner-occupied"),
    "renters_paying_30_percent_or_more": Derived(
        "profile", "DP04", (GRAPI + r"!!30\.0 to 34\.9 percent", GRAPI + r"!!35\.0 percent or more"),
        (GRAPI,), 100,
        "DP04 publishes the 30.0-34.9% and 35.0%+ bands separately; their estimates are summed "
        "and divided by units paying rent where GRAPI is computed."),
    "crowded_homes_pct": Derived(
        "profile", "DP04", (OCCUPANTS + r"!!1\.01 to 1\.50", OCCUPANTS + r"!!1\.51 or more"),
        (OCCUPANTS,), 100,
        "DP04 publishes the 1.01-1.50 and 1.51+ occupants-per-room bands separately; their "
        "estimates are summed and divided by occupied housing units."),
    "uninsured_pct": Direct(
        "subject", "S2701", r"Estimate!!Percent Uninsured!!Civilian noninstitutionalized population"),
    "limited_english_households_pct": Direct(
        "subject", "S1602", r"Estimate!!Percent limited English-speaking households!!All households"),
    "no_internet_access_pct": Derived(
        "detailed", "B28002", (r"Estimate!!Total:!!No Internet access",), (r"Estimate!!Total:",), 100,
        "No profile or subject table publishes the share of households with no Internet access "
        "(S2801 combines it with access without a subscription); derived from the published "
        "household counts."),
    "no_internet_subscription_pct": Direct(
        "subject", "S2801",
        r"Estimate!!Percent!!Total households!!TYPE OF INTERNET SUBSCRIPTIONS!!Without an Internet subscription"),
}


def measure_names() -> list[str]:
    return list(SPECS)


def find_variable(variables: dict, group: str, label_pattern: str, year: int) -> str:
    """Return the one estimate variable of `group` whose label fully matches the pattern."""
    pattern = re.compile(label_pattern.replace("{year}", str(year)))
    matches = [name for name, meta in variables.items()
               if meta.get("group") == group and re.fullmatch(r"[A-Z0-9_]+P?E", name)
               and pattern.fullmatch(meta.get("label", ""))]
    if len(matches) != 1:
        raise LookupError(f"{year} {group}: {len(matches)} variables match {label_pattern!r}: {matches}")
    return matches[0]


def parse_value(raw) -> float:
    """Convert an API cell to a number; annotation codes (negative sentinels) are rejected."""
    if raw is None:
        raise ValueError("missing value")
    value = float(raw)
    if value < 0:
        raise ValueError(f"annotation code {raw}")
    return value


def _get_json(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "census-compass-fixtures"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read().decode("utf-8"))


def _variables(year: int, endpoint: str, cache: dict) -> dict:
    key = (year, endpoint)
    if key not in cache:
        url = API.format(year=year, path=ENDPOINTS[endpoint]) + "/variables.json"
        cache[key] = _get_json(url)["variables"]
    return cache[key]


def _api_values(year: int, endpoint: str, names: list[str], geography: str,
                key: str) -> dict[str, float]:
    geo_key, geo_value = geography.split(":")
    out = {}
    for start in range(0, len(names), 45):
        chunk = names[start:start + 45]
        query = urllib.parse.urlencode(
            {"get": ",".join(chunk), "for": f"{geo_key}:{geo_value}", "key": key})
        rows = _get_json(API.format(year=year, path=ENDPOINTS[endpoint]) + "?" + query)
        if len(rows) != 2:
            raise ValueError(f"expected one row for {geography}, got {len(rows) - 1}")
        header, row = rows[0], rows[1]
        for name in chunk:
            out[name] = parse_value(row[header.index(name)])
    return out


def _table_values(year: int, endpoint: str, group: str, names: list[str],
                  geography: str) -> dict[str, float]:
    """Read one published table for one place from data.census.gov."""
    geo_id = GEO_IDS[geography]
    query = urllib.parse.urlencode({"id": f"{TABLE_PREFIX[endpoint]}{year}.{group}", "g": geo_id})
    rows = _get_json(DATA_CENSUS.format(query=query))["response"]["data"]
    if len(rows) != 2:
        raise ValueError(f"expected one row for {geography}, got {len(rows) - 1}")
    record = dict(zip(rows[0], rows[1]))
    if record.get("GEO_ID") != geo_id:
        raise ValueError(f"unexpected geography {record.get('GEO_ID')!r} for {geo_id}")
    return {name: parse_value(record.get(name)) for name in names}


def _values(year: int, endpoint: str, names: list[str], geography: str,
            groups: dict[str, str]) -> dict[str, float]:
    key = os.environ.get("CENSUS_API_KEY")
    if key:
        return _api_values(year, endpoint, names, geography, key)
    out = {}
    for group in sorted({groups[n] for n in names}):
        out.update(_table_values(year, endpoint, group,
                                 [n for n in names if groups[n] == group], geography))
    return out


def _plan(year: int, cache: dict) -> dict[str, dict]:
    """Resolve every spec to variable names and labels for one year."""
    plan = {}
    for measure, spec in SPECS.items():
        variables = _variables(year, spec.endpoint, cache)
        if isinstance(spec, Direct):
            name = find_variable(variables, spec.group, spec.label, year)
            plan[measure] = {"spec": spec, "names": [name],
                             "labels": {name: variables[name]["label"]},
                             "groups": {name: variables[name]["group"]}}
            continue
        den_group = spec.denominator_group or spec.group
        parts = {
            "numerator": [find_variable(variables, spec.group, p, year) for p in spec.numerator],
            "denominator": [find_variable(variables, den_group, p, year) for p in spec.denominator],
            "subtract": [find_variable(variables, den_group, p, year) for p in spec.subtract],
        }
        names = parts["numerator"] + parts["denominator"] + parts["subtract"]
        plan[measure] = {"spec": spec, "parts": parts, "names": names,
                         "labels": {n: variables[n]["label"] for n in names},
                         "groups": {n: variables[n]["group"] for n in names}}
    return plan


def _expression(parts: dict, scale: float) -> str:
    num = " + ".join(parts["numerator"])
    den = " + ".join(parts["denominator"]) + "".join(f" - {n}" for n in parts["subtract"])
    prefix = f"{scale:g} * " if scale != 1 else ""
    return f"{prefix}({num}) / ({den})"


def build_year(year: int, cache: dict) -> list[dict]:
    plan = _plan(year, cache)
    by_endpoint: dict[str, set[str]] = {}
    groups: dict[str, str] = {}
    for item in plan.values():
        by_endpoint.setdefault(item["spec"].endpoint, set()).update(item["names"])
        groups.update(item["groups"])
    entries = []
    for geography, api_geo in GEOGRAPHIES.items():
        values = {}
        for endpoint, names in by_endpoint.items():
            values.update(_values(year, endpoint, sorted(names), api_geo, groups))
        for measure, item in plan.items():
            spec = item["spec"]
            entry = {"measure": measure, "geography": geography, "year": year,
                     "endpoint": f"/data/{year}/acs/acs5{ENDPOINTS[spec.endpoint]}"}
            if isinstance(spec, Direct):
                name = item["names"][0]
                entry.update(variable=name, label=item["labels"][name], value=values[name])
            else:
                parts = item["parts"]
                num = sum(values[n] for n in parts["numerator"])
                den = (sum(values[n] for n in parts["denominator"])
                       - sum(values[n] for n in parts["subtract"]))
                if den <= 0:
                    raise ValueError(f"{measure} {geography} {year}: denominator {den}")
                entry.update(
                    variable=_expression(parts, spec.scale),
                    label=" | ".join(item["labels"][n] for n in item["names"]),
                    value=round(spec.scale * num / den, 4),
                    derived=True, reason=spec.reason,
                    components=[{"variable": n, "label": item["labels"][n], "value": values[n]}
                                for n in item["names"]])
            entries.append(entry)
    return entries


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", type=int, nargs="+", default=list(YEARS))
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    cache: dict = {}
    for year in args.years:
        entries = build_year(year, cache)
        path = args.out / f"{year}.json"
        path.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {len(entries)} entries to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
