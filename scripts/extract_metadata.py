"""Extract live schema and field dictionaries using existing local Keychain access.

Run from the project root: python -m scripts.extract_metadata
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import keyring
from keyring.backends.macOS import Keyring

from scripts.snowflake_access import DATABASE, SERVICE, USER, connect


def fetch(connection, sql):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        names = [item[0] for item in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()], cursor.sfqid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/metadata/snapshot.json"))
    args = parser.parse_args()
    keyring.set_keyring(Keyring())
    password = keyring.get_password(SERVICE, USER)
    if password is None:
        raise SystemExit("Run scripts/snowflake_access.py --setup in your local Terminal first.")

    schema = "PUBLIC"
    query_ids = {}
    with connect(password) as connection:
        tables, query_ids["tables"] = fetch(connection, f"""
            SELECT TABLE_NAME, TABLE_TYPE, COMMENT
            FROM {DATABASE}.INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA = 'PUBLIC' ORDER BY TABLE_NAME
        """)
        columns, query_ids["columns"] = fetch(connection, f"""
            SELECT TABLE_NAME, ORDINAL_POSITION, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COMMENT
            FROM {DATABASE}.INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = 'PUBLIC' ORDER BY TABLE_NAME, ORDINAL_POSITION
        """)
        dictionaries = {}
        for table in tables:
            name = table["TABLE_NAME"]
            if name.endswith("_FIELD_DESCRIPTIONS"):
                quoted = '"' + name.replace('"', '""') + '"'
                dictionaries[name], query_ids[name] = fetch(
                    connection, f"SELECT * FROM {DATABASE}.{schema}.{quoted}"
                )

    snapshot = {
        "database": DATABASE, "schema": schema,
        "extracted_at": datetime.now(timezone.utc).isoformat(),
        "query_ids": query_ids, "tables": tables, "columns": columns,
        "dictionaries": dictionaries,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(snapshot, indent=2, default=str) + "\n")
    temporary.replace(args.output)
    print(json.dumps({
        "snapshot": str(args.output), "tables": len(tables), "columns": len(columns),
        "dictionary_rows": {name: len(rows) for name, rows in dictionaries.items()},
    }, indent=2))


if __name__ == "__main__":
    main()
