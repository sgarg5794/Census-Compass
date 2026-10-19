"""Download Census API group variable definitions (raw, unchanged) for provenance."""

import urllib.request
from pathlib import Path

YEARS = (2019, 2020)
GROUPS = (
    "B01002E", "B11001E", "B11002E", "B14007E", "B19025E", "B19301E",
    "B19313E", "B25003E", "B28009E", "C15010E", "C24010E",
)
URL = "https://api.census.gov/data/{year}/acs/acs5/groups/{group}.json"
OUT_DIR = Path("definitions/source/census_api")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for year in YEARS:
        for group in GROUPS:
            with urllib.request.urlopen(URL.format(year=year, group=group), timeout=60) as response:
                body = response.read()
            (OUT_DIR / f"{year}_{group}.json").write_bytes(body)
            print(f"{year} {group}: {len(body)} bytes")


if __name__ == "__main__":
    main()
