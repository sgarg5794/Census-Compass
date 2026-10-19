"""execute_query: the only path from the LLM to Snowflake (spec §9).

validate (fail-closed) -> add completeness -> run within the request budget -> split rows and
completeness. Every failure maps to a defined ``Outcome``; messages never carry credentials,
connection strings or stack traces.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, MutableMapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from google.adk.tools import ToolContext
from snowflake.connector.errors import Error as SnowflakeError
from snowflake.connector.errors import InterfaceError, OperationalError

from census_compass.budget import RequestBudget
from census_compass.config import Settings, get_settings
from census_compass.contracts import (
    BuilderMetadata,
    Outcome,
    QueryRejected,
    QueryResult,
    load_allowed_tables,
    load_builder_metadata,
)
from census_compass.database.completeness import AugmentedQuery, augment, split_result
from census_compass.database.connection import cancel, open_connection, run_statement
from census_compass.database.validation import validate_and_fix

log = logging.getLogger(__name__)

YEARS = (2019, 2020)
DEADLINE_KEY = "temp:budget_deadline"
ATTEMPTS_KEY = "temp:sql_attempts"

_TIMEOUT_ERRNOS = {604, 630}  # SQL execution canceled; statement/warehouse timeout
_SESSION_ERRNOS = {390111, 390112, 390114}  # session gone / expired / token expired
_DB_FAILURE_MESSAGE = (
    "The census database could not be reached or the login failed. "
    "Tell the user the data is temporarily unavailable; do not retry."
)
_TIMEOUT_MESSAGE = (
    "The query ran out of the request's time budget and was stopped. "
    "Tell the user the question took too long; suggest a narrower question."
)

_lock = threading.Lock()
_conn: Any = None
_metadata_cache: dict[Path, tuple[BuilderMetadata, list[str]]] = {}


def execute_query(sql: str, tool_context: ToolContext) -> dict:
    """Run one read-only Snowflake SELECT over the census tables and return its rows.

    This is the only way to query the data. Write one SELECT (CTEs, subqueries, joins to the
    FIPS table, GROUP BY, window functions and LIMIT are fine). Before it runs, the query is
    validated automatically (allowed tables and columns only, one year, no dimension
    aggregation; quoting, fully qualified names and a row limit are fixed for you) and
    completeness columns are added automatically for every aggregate output -- do not compute
    block-group counts or coverage yourself.

    Rules:
    - Filter geography with the exact ``sql_filter`` returned by resolve_geography.
    - Copy each member's ``sql`` from the schema definitions exactly; do not rewrite it.

    Returns a dict with:
    - ``outcome``: one of answered, clarification_needed, not_available, unsupported,
      query_rejected, timed_out, database_failure, model_failure, truncated.
    - ``rows``: list of row objects (column -> value); completeness columns removed.
    - ``completeness``: one entry per row and aggregate column: ``block_groups``,
      ``missing_inputs`` (input column -> block groups with it missing) and, where relevant,
      ``coverage``, ``unknown_denominator``, ``missing_moe``. Use it to qualify the answer.
    - ``truncated``: true when rows were cut at the row limit (outcome truncated); say so.
    - ``message``: why the query was rejected (with suggestions) or other guidance;
      "no rows" when the query returned nothing.
    - ``query_id``: the Snowflake query ID, when the query ran.

    On query_rejected, fix the SQL using the message and call again. At most 2 retries
    (3 calls) per question; after that the call returns query_rejected "retry limit reached"
    and you must stop and explain. On timed_out or database_failure, do not retry.
    """
    return run_query(sql, tool_context.state)


def reset() -> None:
    """Drop the shared connection and metadata cache (tests and shutdown)."""
    global _conn
    with _lock:
        _close(_conn)
        _conn = None
        _metadata_cache.clear()


def run_query(
    sql: str,
    state: MutableMapping[str, Any],
    *,
    settings: Settings | None = None,
    semantic_dir: Path | None = None,
    connect: Callable[..., Any] | None = None,
    get_password: Callable[[str, str], str | None] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Implementation of ``execute_query`` with injectable dependencies."""
    settings = settings or get_settings()
    attempts = int(state.get(ATTEMPTS_KEY) or 0) + 1
    state[ATTEMPTS_KEY] = attempts
    if attempts > settings.max_sql_retries + 1:
        return _result(
            Outcome.QUERY_REJECTED,
            message=(
                "retry limit reached: the query was rejected too many times. Stop and tell the "
                "user the question could not be answered with a valid query."
            ),
        )

    deadline = state.get(DEADLINE_KEY)
    if deadline is None:
        budget = RequestBudget(settings.request_budget_seconds, clock)
    else:
        budget = RequestBudget(float(deadline) - clock(), clock)

    metadata, allowed = _semantics(Path(semantic_dir or settings.semantic_dir))
    try:
        # One row above the cap so run_statement can detect (and report) truncation.
        checked = validate_and_fix(
            sql,
            metadata,
            allowed,
            settings.max_result_rows + 1,
            settings.snowflake_database,
            settings.snowflake_schema,
        )
        augmented = augment(checked, metadata)
    except QueryRejected as e:
        return _rejected(e)

    opener = _opener(settings, connect, get_password)
    with _lock:
        return _execute(augmented, budget, settings, opener)


# --------------------------------------------------------------------------- execution


def _execute(
    augmented: AugmentedQuery,
    budget: RequestBudget,
    settings: Settings,
    opener: Callable[[], Any],
) -> dict:
    global _conn
    max_rows = settings.max_result_rows
    reopened = False
    while True:
        timeout = budget.statement_timeout(settings.min_statement_timeout_seconds)
        if timeout is None:
            return _result(Outcome.TIMED_OUT, message=_TIMEOUT_MESSAGE)
        if _conn is None:
            try:
                _conn = opener()
            except Exception as e:  # login, network, Keychain, role mismatch
                log.warning("Snowflake connection failed: %s", type(e).__name__)
                return _result(Outcome.DATABASE_FAILURE, message=_DB_FAILURE_MESSAGE)
        try:
            columns, rows, truncated, query_id = run_statement(
                _conn, augmented.sql, timeout, max_rows
            )
            break
        except Exception as e:
            query_id = getattr(e, "sfqid", None) or None
            if _is_timeout(e):
                if query_id:
                    _cancel(query_id)
                return _result(Outcome.TIMED_OUT, message=_TIMEOUT_MESSAGE, query_id=query_id)
            if _is_connection_error(e):
                log.warning("Snowflake connection error: %s", type(e).__name__)
                _close(_conn)
                _conn = None
                if query_id is None and not reopened:
                    reopened = True
                    continue  # the statement never started: reopen once and retry
                return _result(
                    Outcome.DATABASE_FAILURE, message=_DB_FAILURE_MESSAGE, query_id=query_id
                )
            if isinstance(e, SnowflakeError) and query_id:
                return _result(
                    Outcome.QUERY_REJECTED,
                    message="Snowflake rejected the query: " + _snowflake_message(e, settings),
                    query_id=query_id,
                )
            log.warning("Unexpected query failure: %s", type(e).__name__)
            return _result(Outcome.DATABASE_FAILURE, message=_DB_FAILURE_MESSAGE, query_id=query_id)

    try:
        out_rows, completeness = split_result(columns, rows, augmented.companions)
    except ValueError:
        log.warning("Completeness columns missing from result %s", query_id)
        return _result(
            Outcome.DATABASE_FAILURE,
            message="The result came back without its completeness information and was withheld.",
            query_id=query_id,
        )
    out_rows = [{k: _json_value(v) for k, v in row.items()} for row in out_rows]
    if truncated:
        return _result(
            Outcome.TRUNCATED,
            rows=out_rows,
            completeness=completeness,
            truncated=True,
            message=(
                f"Result truncated: only the first {max_rows} rows were returned (row limit). "
                "State in the answer that the result is incomplete."
            ),
            query_id=query_id,
        )
    return _result(
        Outcome.ANSWERED,
        rows=out_rows,
        completeness=completeness,
        message="" if out_rows else "no rows",
        query_id=query_id,
    )


def _opener(
    settings: Settings,
    connect: Callable[..., Any] | None,
    get_password: Callable[[str, str], str | None] | None,
) -> Callable[[], Any]:
    kwargs: dict[str, Any] = {}
    if connect is not None:
        kwargs["connect"] = connect
    if get_password is not None:
        kwargs["get_password"] = get_password
    return lambda: open_connection(settings, **kwargs)


def _cancel(query_id: str) -> None:
    try:
        cancel(_conn, query_id)
    except Exception as e:
        log.warning("Cancelling query %s failed: %s", query_id, type(e).__name__)


def _close(conn: Any) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def _is_timeout(e: Exception) -> bool:
    if not isinstance(e, SnowflakeError):
        return False
    msg = (e.msg or "").lower()
    return e.errno in _TIMEOUT_ERRNOS or "timeout" in msg or "canceled" in msg


def _is_connection_error(e: Exception) -> bool:
    if isinstance(e, (OperationalError, InterfaceError, OSError)):
        return True
    if isinstance(e, SnowflakeError):
        return e.errno in _SESSION_ERRNOS or (e.sqlstate or "").startswith("08")
    return False


def _snowflake_message(e: SnowflakeError, settings: Settings) -> str:
    lines = (e.msg or type(e).__name__).strip().splitlines() or [type(e).__name__]
    msg = lines[0][:300]
    for secret in (settings.snowflake_account, settings.snowflake_user):
        if secret:
            msg = msg.replace(secret, "***")
    return msg


# --------------------------------------------------------------------------- helpers


def _semantics(semantic_dir: Path) -> tuple[BuilderMetadata, list[str]]:
    key = semantic_dir.resolve()
    with _lock:
        cached = _metadata_cache.get(key)
    if cached is not None:
        return cached
    per_year = [load_builder_metadata(semantic_dir, year) for year in YEARS]
    table_columns: dict[str, list[str]] = {}
    for m in per_year:
        table_columns.update(m.table_columns)
    merged = BuilderMetadata(
        year=per_year[-1].year,
        members=[member for m in per_year for member in m.members],
        table_columns=table_columns,
        fips_table=per_year[-1].fips_table,
    )
    tables = load_allowed_tables(semantic_dir).tables
    allowed = [t for year in YEARS for t in tables.get(year, [])]
    with _lock:
        _metadata_cache[key] = (merged, allowed)
    return merged, allowed


def _rejected(e: QueryRejected) -> dict:
    message = e.reason
    if e.suggestions:
        message += " Suggestions: " + "; ".join(e.suggestions)
    return _result(Outcome.QUERY_REJECTED, message=message)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


def _result(outcome: Outcome, **fields: Any) -> dict:
    return QueryResult(outcome=outcome, **fields).model_dump(mode="json")
