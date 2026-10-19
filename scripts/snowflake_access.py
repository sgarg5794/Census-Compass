"""Local Census access using a password held in the OS credential store (macOS Keychain)."""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

import keyring
import snowflake.connector

from census_compass.config import get_settings

_SETTINGS = get_settings()  # SNOWFLAKE_* values from .env or the environment
ACCOUNT = _SETTINGS.snowflake_account
USER = _SETTINGS.snowflake_user
ROLE = _SETTINGS.snowflake_role
WAREHOUSE = _SETTINGS.snowflake_warehouse
DATABASE = _SETTINGS.snowflake_database
SERVICE = "census-compass-snowflake:" + ACCOUNT
TEST_SQL = (
    'SELECT CURRENT_ROLE() AS ACTIVE_ROLE, COUNT(*) AS ROW_COUNT '
    'FROM "' + DATABASE + '".PUBLIC."2020_METADATA_CBG_FIPS_CODES"'
)


def connect(password):
    connection = snowflake.connector.connect(
        account=ACCOUNT,
        user=USER,
        password=password,
        authenticator=os.environ.get("CENSUS_SNOWFLAKE_AUTHENTICATOR", "snowflake"),
        role=ROLE,
        warehouse=WAREHOUSE,
        database=DATABASE,
        schema="PUBLIC",
        login_timeout=60,
        network_timeout=60,
        session_parameters={
            "STATEMENT_TIMEOUT_IN_SECONDS": 45,
            "QUERY_TAG": "census_compass_exploration",
        },
    )
    try:
        with connection.cursor() as cursor:
            cursor.execute("USE SECONDARY ROLES NONE")
            cursor.execute("SELECT CURRENT_ROLE()")
            if cursor.fetchone()[0] != ROLE:
                raise RuntimeError("Unexpected active role")
        return connection
    except Exception:
        connection.close()
        raise


def query(connection, sql, limit):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        rows = cursor.fetchmany(limit + 1)
        return {
            "columns": [column[0] for column in cursor.description],
            "rows": rows[:limit],
            "truncated": len(rows) > limit,
            "query_id": cursor.sfqid,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_mutually_exclusive_group()
    commands.add_argument("--setup", action="store_true")
    commands.add_argument("--sql")
    commands.add_argument("--file", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()
    if args.limit < 1 or args.limit > 10000:
        parser.error("--limit must be between 1 and 10000")

    if sys.platform == "darwin":  # never fall back to a plaintext store on macOS
        from keyring.backends.macOS import Keyring

        keyring.set_keyring(Keyring())
    if args.setup:
        if not sys.stdin.isatty():
            parser.error("Run --setup in your local Terminal for hidden password entry")
        password = getpass.getpass("Snowflake password (hidden; saved in your OS credential store): ")
        if not password:
            parser.error("Password cannot be empty")
        with connect(password) as connection:
            result = query(connection, TEST_SQL, 1)
        if result["rows"] != [(ROLE, 3234)]:
            raise RuntimeError("Connection test returned an unexpected result")
        keyring.set_password(SERVICE, USER, password)
        print(f"Verified {ROLE} access: 3234 rows. Password saved in your OS credential store.")
        return

    password = keyring.get_password(SERVICE, USER)
    if password is None:
        print("No saved credential. Run this script with --setup in your local Terminal.")
        sys.exit(2)
    sql = args.file.read_text() if args.file else (args.sql or TEST_SQL)
    with connect(password) as connection:
        print(json.dumps(query(connection, sql, args.limit), default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # Avoid dumping connection objects or authentication details into logs.
        print(
            "Connection/query failed: " + type(error).__name__
            + "; code=" + str(getattr(error, "errno", None))
            + "; SQLSTATE=" + str(getattr(error, "sqlstate", None)),
            file=sys.stderr,
        )
        sys.exit(1)
