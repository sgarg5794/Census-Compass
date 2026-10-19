"""Snowflake connection lifecycle: Keychain password, pinned role, bounded statements."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

import snowflake.connector

_QUERY_ID = re.compile(r"^[0-9a-f-]+$")


def _keychain_password(account: str, user: str) -> str | None:
    import keyring
    import keyring.backends.macOS

    keyring.set_keyring(keyring.backends.macOS.Keyring())
    return keyring.get_password("census-compass-snowflake:" + account, user)


def open_connection(
    settings: Any,
    connect: Callable[..., Any] = snowflake.connector.connect,
    get_password: Callable[[str, str], str | None] = _keychain_password,
) -> Any:
    password = get_password(settings.snowflake_account, settings.snowflake_user)
    if password is None:
        raise RuntimeError(
            "No Snowflake password in the macOS Keychain; run scripts/snowflake_access.py --setup"
        )
    conn = connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        password=password,
        role=settings.snowflake_role,
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        schema=settings.snowflake_schema,
        login_timeout=10,
        network_timeout=30,
        session_parameters={"QUERY_TAG": "census_compass_agent"},
    )
    try:
        cur = conn.cursor()
        cur.execute("USE SECONDARY ROLES NONE")
        cur.execute("SELECT CURRENT_ROLE()")
        row = cur.fetchone()
        role = row[0] if row else None
    except Exception:
        conn.close()
        raise
    if role != settings.snowflake_role:
        conn.close()
        raise RuntimeError(
            f"Snowflake session role is {role!r}, expected {settings.snowflake_role!r}"
        )
    return conn


def run_statement(
    conn: Any, sql: str, timeout_s: int, max_rows: int
) -> tuple[list[str], list[tuple], bool, str]:
    cur = conn.cursor()
    cur.execute(f"ALTER SESSION SET STATEMENT_TIMEOUT_IN_SECONDS = {int(timeout_s)}")
    cur.execute(sql)
    query_id = cur.sfqid
    columns = [d[0] for d in (cur.description or [])]
    fetched = cur.fetchmany(max_rows + 1)
    truncated = len(fetched) > max_rows
    return columns, [tuple(r) for r in fetched[:max_rows]], truncated, query_id


def cancel(conn: Any, query_id: str) -> None:
    if not _QUERY_ID.fullmatch(query_id):
        raise ValueError("invalid query id")
    conn.cursor().execute(f"SELECT SYSTEM$CANCEL_QUERY('{query_id}')")
