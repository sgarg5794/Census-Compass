"""Extract the county lookup (with demographic block-group counts) per year. Read-only SQL."""

import json
from pathlib import Path

import keyring
from keyring.backends.macOS import Keyring

import scripts.snowflake_access as helper

YEARS = (2019, 2020)
OUT_DIR = Path("definitions/source/geography")
TERRITORY_ABBR = {"60": "AS", "66": "GU", "69": "MP", "78": "VI"}

SQL = """
SELECT f."STATE", f."STATE_FIPS", f."COUNTY_FIPS", f."COUNTY", COALESCE(c.BG, 0)
FROM "{year}_METADATA_CBG_FIPS_CODES" f
LEFT JOIN (
    SELECT LEFT("CENSUS_BLOCK_GROUP", 5) AS PREFIX, COUNT(*) AS BG
    FROM "{year}_CBG_B01"
    GROUP BY 1
) c ON c.PREFIX = f."STATE_FIPS" || f."COUNTY_FIPS"
ORDER BY f."STATE_FIPS", f."COUNTY_FIPS"
"""


def _record(row: tuple) -> dict:
    abbr, state_fips, county_fips, county, block_groups = row
    where = f"state_fips={state_fips!r} county_fips={county_fips!r}"
    if abbr is None and state_fips in TERRITORY_ABBR:
        abbr = TERRITORY_ABBR[state_fips]
    for name, value in (("STATE", abbr), ("STATE_FIPS", state_fips),
                        ("COUNTY_FIPS", county_fips), ("COUNTY", county)):
        if value is None:
            raise SystemExit(f"NULL {name} for {where}; refusing to write geography file")
    return {
        "state_abbr": abbr,
        "state_fips": state_fips,
        "county_fips": county_fips,
        "county": county,
        "block_groups": int(block_groups),
    }


def main() -> None:
    keyring.set_keyring(Keyring())
    password = keyring.get_password(helper.SERVICE, helper.USER)
    if password is None:
        raise SystemExit("No saved credential. Run snowflake_access.py --setup locally.")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with helper.connect(password) as connection:
        for year in YEARS:
            with connection.cursor() as cursor:
                cursor.execute(SQL.format(year=year))
                rows = cursor.fetchall()
            records = [_record(r) for r in rows]
            records.sort(key=lambda r: (r["state_fips"], r["county_fips"]))
            (OUT_DIR / f"{year}.json").write_text(
                json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            print(f"{year}: {len(records)} records, "
                  f"{sum(r['block_groups'] for r in records)} block groups")


if __name__ == "__main__":
    main()
