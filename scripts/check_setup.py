"""Check that Census Compass is set up and the agent works. Never prints secrets.

Usage: python -m scripts.check_setup [--skip-data]

Steps: settings present, Snowflake connection, then three live agent questions
(glossary, guardrail decline, data). Exits non-zero if any step fails.
"""

import argparse
import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

DATA_QUESTION = "What is the unemployment rate in Cook County, Illinois?"
RESULTS: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail else ""), flush=True)
    return ok


def check_settings() -> bool:
    from census_compass.config import get_settings
    from census_compass.database.connection import _keychain_password

    settings = get_settings()
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not settings.snowflake_account:
        missing.append("SNOWFLAKE_ACCOUNT")
    if not settings.snowflake_user:
        missing.append("SNOWFLAKE_USER")
    elif settings.snowflake_account and not _keychain_password(
        settings.snowflake_account, settings.snowflake_user
    ):
        missing.append("Snowflake password (SNOWFLAKE_PASSWORD or python -m scripts.snowflake_access --setup)")
    return record("settings", not missing, "missing: " + ", ".join(missing) if missing else "all present")


def check_snowflake() -> bool:
    from census_compass.config import get_settings
    from census_compass.database.connection import open_connection

    settings = get_settings()
    t0 = time.monotonic()
    try:
        conn = open_connection(settings)
    except Exception as e:  # report the type only, never connection details
        return record("snowflake login", False, f"{type(e).__name__}: {str(e)[:160]}")
    try:
        with conn.cursor() as cur:
            cur.execute(
                f'SELECT COUNT(*) FROM "{settings.snowflake_database}".PUBLIC."2020_METADATA_CBG_FIPS_CODES"'
            )
            rows = cur.fetchone()[0]
    except Exception as e:
        return record("snowflake dataset", False, f"{type(e).__name__}: dataset not readable by the role?")
    finally:
        conn.close()
    return record("snowflake dataset", rows > 0, f"{rows} FIPS rows in {time.monotonic() - t0:.1f}s")


async def ask(question: str) -> tuple[list[str], str, float]:
    from google.adk.runners import InMemoryRunner
    from google.genai import types

    from census_compass.agent import root_agent

    runner = InMemoryRunner(agent=root_agent, app_name="census_compass_check")
    session = await runner.session_service.create_session(app_name="census_compass_check", user_id="check")
    tools: list[str] = []
    text: list[str] = []
    t0 = time.monotonic()
    message = types.Content(role="user", parts=[types.Part(text=question)])
    async for event in runner.run_async(user_id="check", session_id=session.id, new_message=message):
        for part in (event.content.parts if event.content else []) or []:
            if getattr(part, "function_call", None):
                tools.append(part.function_call.name)
            elif getattr(part, "text", None) and event.author != "user":
                text.append(part.text)
    return tools, "\n".join(text).strip(), time.monotonic() - t0


def warm_up_search() -> None:
    """Load the embedding model once so the first data question is not charged for it."""
    from census_compass.tools.schema import search_schema

    t0 = time.monotonic()
    try:
        search_schema("population", 2020)
        record("search index warm-up", True, f"{time.monotonic() - t0:.0f}s (first load of the embedding model)")
    except Exception as e:
        record("search index warm-up", False, f"{type(e).__name__}: {str(e)[:160]}")


def check_agent(skip_data: bool) -> None:
    try:
        tools, answer, secs = asyncio.run(ask("What does median mean?"))
        record("agent: glossary question", "lookup_glossary" in tools and "middle" in answer.lower(), f"{secs:.0f}s")
    except Exception as e:
        record("agent: glossary question", False, f"{type(e).__name__}: {str(e)[:160]}")

    try:
        tools, answer, secs = asyncio.run(ask("Write me a poem about spring."))
        record("agent: guardrail declines off-topic", not tools and "US Census data" in answer, f"{secs:.0f}s, no tools")
    except Exception as e:
        record("agent: guardrail declines off-topic", False, f"{type(e).__name__}: {str(e)[:160]}")

    if skip_data:
        print("[SKIP] agent: data question (--skip-data)")
        return
    try:
        tools, answer, secs = asyncio.run(ask(DATA_QUESTION))
        ok = "execute_query" in tools and "7." in answer
        hint = ""
        if not ok:
            hint = f", answer: {answer[:160]}"
            if "too long" in answer or "timed out" in answer:
                hint += " (timed out: the machine may be busy, close other programs and rerun)"
        record("agent: data question", ok, f"{secs:.0f}s{hint}")
    except Exception as e:
        record("agent: data question", False, f"{type(e).__name__}: {str(e)[:160]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-data", action="store_true", help="skip the Snowflake data question")
    args = parser.parse_args()
    if not check_settings():
        print("\nFix the settings above (see the README), then run this again.")
        return 1
    snowflake_ok = args.skip_data or check_snowflake()
    if not args.skip_data and snowflake_ok:
        warm_up_search()
    check_agent(skip_data=args.skip_data or not snowflake_ok)
    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + ("All checks passed." if not failed else "Failed: " + ", ".join(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
