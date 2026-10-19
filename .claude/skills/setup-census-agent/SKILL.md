---
name: setup-census-agent
description: Set up the Census Compass agent on the local machine and get it running. Trigger phrases - "setup census agent", "setup census agent on local", "set up Census Compass locally". Installs dependencies, guides the user to add their Anthropic key and Snowflake credentials without pasting secrets into chat, runs the setup check and unit tests, then starts the chat UI.
---

# Set up the Census agent locally

Get a fresh checkout of this repository running, then prove the agent works.

## Rules

- Never ask the user to paste an API key or password into the chat, and never read, print or echo `.env` or any secret. The user types secrets themselves, into `.env` or at a hidden prompt.
- Work from the repository root. Use the virtual environment's Python (`.venv/bin/python`, or `.venv\Scripts\python` on Windows), not the system Python.
- Stop and tell the user what failed instead of retrying the same command.

## Steps

1. **Check Python.** Run `python3 --version`. It must be 3.12 or 3.13. If not, tell the user to install one and stop.

2. **Install** (skip any part that already exists):
   ```sh
   python3 -m venv .venv
   .venv/bin/python -m pip install -r requirements.lock.txt
   .venv/bin/python -m pip install --no-deps -e .
   ```
   The generated definitions (`definitions/generated/`) and search index (`data/index/`) are committed. Nothing else needs building.

3. **Create `.env`** if it does not exist: `cp .env.example .env`.

4. **Ask the user to add credentials.** Tell them, then wait for them to reply "done":
   - Open `.env` and set `ANTHROPIC_API_KEY=` to their Anthropic API key (no quotes).
   - They need a Snowflake account that has the free dataset
     [SafeGraph: US Open Census Data & Neighborhood Insights](https://app.snowflake.com/marketplace/listing/GZSNZ2UNN0/safegraph-us-open-census-data-neighborhood-insights-free-dataset)
     added as a database named `US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET`, and a role that can read it. The README (step 3) has the SQL to create the `CENSUS_READER` role. If they have no account yet, point them to https://signup.snowflake.com/.
   - Set `SNOWFLAKE_ACCOUNT=` and `SNOWFLAKE_USER=` in `.env`. Adjust `SNOWFLAKE_ROLE` and `SNOWFLAKE_WAREHOUSE` if theirs differ from the defaults.
   - Give the Snowflake password in one of two ways: set `SNOWFLAKE_PASSWORD=` in `.env`, or have them run `! .venv/bin/python -m scripts.snowflake_access --setup` in this session (hidden prompt, stores it in the OS credential store; tested on macOS).
   - If they want to try the app without Snowflake, say that glossary answers, small talk and declines still work but data questions will not, and use `--skip-data` in step 5.

5. **Run the setup check:**
   ```sh
   .venv/bin/python -m scripts.check_setup
   ```
   It prints PASS or FAIL per step and never prints secrets: settings present, Snowflake login and dataset access, then live agent questions (a glossary question, an off-topic question that must be declined with no tools, and a data question). If a step fails, explain the likely cause from its message (missing key, wrong account, role without dataset access) and ask the user to fix it, then rerun. Do not continue to step 6 until it passes (or the user chose `--skip-data` and everything else passed). The first run loads the embedding model (about 20 seconds, labelled "search index warm-up"), then a data question takes about 15 to 30 seconds. If the data question times out, the machine is probably busy: ask the user to close heavy programs and rerun.

6. **Run the unit tests** (no network, about 3 to 5 minutes, run it in the background and report when finished):
   ```sh
   .venv/bin/python -m pytest tests/unit -q
   ```
   Report the pass count. If anything fails, show the failing test names.

7. **Start the app** in the background and give the user the URL:
   ```sh
   .venv/bin/python -m streamlit run ui/app.py
   ```
   The UI is at http://localhost:8501. The ADK developer UI alternative is `.venv/bin/adk web`. Suggest a first question: "What is the unemployment rate in Cook County, Illinois?"

## Done when

The setup check passes, the unit tests pass, and the UI is running. Summarize what passed and anything skipped.
