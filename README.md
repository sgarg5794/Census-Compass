# Census Compass

A conversational agent that answers plain-language questions about US demographics
using the US Census data in Snowflake. Every answer says which survey period it
comes from, how complete the data is, and what the agent could not tell you.

**Live demo:** https://census-compass-342695199442.us-east1.run.app/

The demo answers from the 2015–2019 and 2016–2020 ACS 5-year estimates and the 2020 Census count, so figures are not current. The first question after a quiet period can take longer than later ones. To run it on your own machine, see [Run it locally](#run-it-locally-general-setup) or the [one-click setup](#one-click-setup-with-claude-code).

## What it does

Ask a question such as *"What is the unemployment rate in Cook County, Illinois?"*
or *"What share of Ohio residents are Black?"*, and follow up in the same
conversation (*"And Texas?"*).

- **Answers from the data.** The agent finds the right Census table, writes a read-only
  SQL query, checks it, runs it against Snowflake and explains the result. It covers
  the nation (the 50 states and DC; Puerto Rico is excluded from national figures),
  states and counties, from the ACS 5-year estimates (2015–2019 and 2016–2020) and
  the 2020 Census count. Cities and ZIP codes are not supported.
- **Explains uncertainty.** Answers state the survey period, the coverage of the data,
  and when a figure is an estimate with a margin of error.
- **Explains Census terms.** Questions like *"What does median mean?"* are answered
  only from a reviewed glossary of 66 entries, each citing a page of an official
  Census Bureau document. Anything not in the glossary gets
  *"That isn't covered in my Census reference material."*
- **Stays on topic.** A small classifier model screens every message before the agent
  runs. Off-topic requests, prompt-injection attempts and value-judgement or
  discriminatory questions get a fixed reply and never reach the agent or Snowflake.
  Factual questions about race, ethnicity, poverty and income are answered.
- **Mixed messages.** The Census part is answered and the rest is skipped with a note.

Screenshots are in [`docs/screenshots/`](docs/screenshots/).

## How it works

Google ADK agent running Claude (Sonnet) through LiteLLM, with internal Python tools:

| Tool | Purpose |
| --- | --- |
| `resolve_year`, `resolve_geography`, `resolve_population_group` | Turn the user's wording into a survey period, a place and a race/ethnicity group |
| `search_schema`, `get_schema` | Find and read the right table definitions (LanceDB hybrid search over `definitions/generated/`) |
| `execute_query` | Validate (sqlglot, fail-closed) and run read-only SQL with a 60-second budget |
| `lookup_glossary` | Answer Census concept questions from the reviewed glossary |

Design documents are in `docs/specs/`.

## One-click setup with Claude Code

If you use [Claude Code](https://claude.com/claude-code), open this repository in it and say
**"setup census agent"** (or "setup census agent on local"). The project skill in
[`.claude/skills/setup-census-agent/`](.claude/skills/setup-census-agent/SKILL.md) takes over:
it installs the dependencies, asks you to add your Anthropic key and Snowflake credentials
(you type them yourself, never into the chat), runs `python -m scripts.check_setup` to
confirm the agent works, runs the unit tests and starts the chat UI. You still need the
Snowflake account and dataset from step 3 below.

Prefer to do it yourself? Use the general setup below.

## Run it locally (general setup)

You need Python 3.12 or 3.13 (check with `python3 --version`), an Anthropic API key, and
a Snowflake account that can read the Census dataset (step 3). Without Snowflake the app
still starts and answers glossary questions, small talk and declines, but it cannot
answer data questions.

1. **Install**

   ```sh
   git clone https://github.com/sgarg5794/Census-Compass.git
   cd Census-Compass
   python3 -m venv .venv
   source .venv/bin/activate        # on Windows: .venv\Scripts\activate
   python -m pip install -r requirements.lock.txt
   python -m pip install --no-deps -e .
   ```

   The generated table definitions (`definitions/generated/`) and the search index
   (`data/index/`) are committed, so there is nothing to build. The first data question
   downloads a small embedding model (BAAI/bge-small-en-v1.5) from Hugging Face, so it
   can take longer than later ones.

2. **Create `.env` and add your Anthropic key**

   ```sh
   cp .env.example .env
   ```

   Open `.env` and put your key after `ANTHROPIC_API_KEY=` (no quotes or spaces). The
   file is git-ignored, and the app reads it automatically at startup. The other values
   in `.env.example` have working defaults.

3. **Set up Snowflake (needed for data questions)**

   The agent runs read-only SQL against a free public dataset, so you need your own
   Snowflake account with that dataset added. The table definitions in this repo only
   describe the dataset (they are the agent's map); the numbers themselves are read from
   the dataset in your Snowflake account.

   1. **Create a Snowflake account** (a free trial works): https://signup.snowflake.com/
   2. **Add the dataset.** In Snowsight open Data Products, Marketplace and get
      [SafeGraph: US Open Census Data & Neighborhood Insights - Free Dataset](https://app.snowflake.com/marketplace/listing/GZSNZ2UNN0/safegraph-us-open-census-data-neighborhood-insights-free-dataset).
      Name the database exactly `US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET`
      (the schema is `PUBLIC`).
   3. **Create a read-only role** by running this in a Snowsight worksheet (replace
      `YOUR_USER` with your Snowflake login name):

      ```sql
      CREATE ROLE IF NOT EXISTS CENSUS_READER;
      GRANT ROLE CENSUS_READER TO USER YOUR_USER;
      GRANT IMPORTED PRIVILEGES ON DATABASE US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET TO ROLE CENSUS_READER;
      GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE CENSUS_READER;
      ```

      Use a different warehouse name if yours is not `COMPUTE_WH`, and set
      `SNOWFLAKE_WAREHOUSE` to match.
   4. **Add your account details to `.env`:** `SNOWFLAKE_ACCOUNT` (for example
      `ORGNAME-ACCOUNTNAME`, shown in Snowsight under your account menu) and
      `SNOWFLAKE_USER` (your login name). `SNOWFLAKE_ROLE` defaults to `CENSUS_READER`.
   5. **Give the app your password** in one of two ways:
      - set `SNOWFLAKE_PASSWORD=...` in `.env` (works on any system), or
      - store it in your OS credential store with
        `python -m scripts.snowflake_access --setup` (hidden prompt; tested on macOS).

   If the account uses MFA, see `docs/snowflake_connection.md`. To check the connection:
   `python -m scripts.snowflake_access`.

4. **Start it**

   Chat UI (Streamlit):

   ```sh
   python -m streamlit run ui/app.py
   ```

   or the ADK developer UI:

   ```sh
   adk web
   ```

   Open the printed URL (Streamlit: http://localhost:8501) and ask, for example,
   "What is the unemployment rate in Cook County, Illinois?". More options:
   `docs/running_the_agent.md`.

### If something goes wrong

| What you see | Likely cause and fix |
| --- | --- |
| An authentication error from the model | `ANTHROPIC_API_KEY` is missing or wrong in `.env`. Restart the app after editing it. |
| Data questions answer "couldn't reach the database" | `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER` or the password is missing or wrong, or the role has no access (step 3). Check with `python -m scripts.snowflake_access`. |
| The first data question is slow or times out | The embedding model is downloading on first use. Ask again. |
| `ModuleNotFoundError: census_compass` | Run the install commands in step 1 from the repository root, with the virtual environment active. |

## Tests and checks

```sh
python -m scripts.check_setup        # quick check that settings, Snowflake and the agent work
python -m pytest tests/unit          # about 3 minutes, no network
ruff check .
python -m scripts.run_guardrail_eval # live classifier evaluation (uses the Anthropic key)
python -m scripts.run_guardrail_e2e  # live end-to-end conversations (uses Anthropic and Snowflake)
```

## Repository layout

- `census_compass/` – agent, tools, guardrails, semantic build, database access, chat service
- `census_compass/guardrails/` – message screening (classifier, gate, fixed replies)
- `definitions/source/` – hand-written inputs, including the reviewed Census glossary
- `definitions/generated/` and `data/index/` – generated table definitions and search index (committed)
- `data/tables/` – extracted table schemas, the input to the definition build
- `ui/` – Streamlit chat interface
- `.claude/skills/` – project skill for one-click setup with Claude Code
- `evaluations/` – evaluation question sets and the guardrail label set
- `scripts/` – extraction, build, evaluation and Snowflake setup scripts
- `docs/` – design, setup, specs and plans

## Rebuilding the definitions or the index

Only needed if you change the source definitions or the embedding model:
see `docs/running_the_agent.md` and `census_compass/semantic/`. Changing the embedding
model requires rebuilding and recommitting `data/index/`.
