# Census Compass

A conversational agent that answers plain-language questions about US demographics
using the US Census data in Snowflake. Every answer says which survey period it
comes from, how complete the data is, and what the agent could not tell you.

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

Design documents are in `docs/design.md` and `docs/superpowers/specs/`.

## Run it locally

You need Python 3.12 or 3.13, a Claude API key, and read access to the Census dataset
in Snowflake (see step 3).

1. **Install**

   ```sh
   git clone https://github.com/sgarg5794/Census-Compass.git
   cd Census-Compass
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.lock.txt
   python -m pip install --no-deps -e .
   ```

   The generated table definitions (`definitions/generated/`) and the search index
   (`data/index/`) are committed, so there is nothing to build. The first run downloads a
   small embedding model (BAAI/bge-small-en-v1.5) from Hugging Face.

2. **Add your Anthropic key**

   ```sh
   cp .env.example .env
   ```

   Then set `ANTHROPIC_API_KEY=...` in `.env`. The file is git-ignored.

3. **Connect Snowflake (needed for data questions)**

   Data questions run SQL in Snowflake, so you need an account that can read the
   database `US_OPEN_CENSUS_DATA_NEIGHBORHOOD_INSIGHTS_FREE_DATASET` (schema `PUBLIC`).
   Put your account, user, role and warehouse in `.env` (see the `SNOWFLAKE_*` entries in
   `.env.example`; the defaults are the project owner's values), then store the password
   in the macOS Keychain, which is where the agent reads it:

   ```sh
   pip install keyring   # if it is not installed
   python scripts/snowflake_access.py --setup
   ```

   Details are in `docs/snowflake_connection.md`. Without Snowflake the agent still
   answers glossary questions, small talk and declines, but cannot answer data questions.

4. **Start it**

   Chat UI (Streamlit):

   ```sh
   python -m streamlit run ui/app.py
   ```

   or the ADK developer UI:

   ```sh
   adk web
   ```

   Open the printed URL. More options: `docs/running_the_agent.md` and `docs/ui.md`.

## Tests and checks

```sh
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
- `evaluations/` – evaluation question sets and the guardrail label set
- `scripts/` – extraction, build, evaluation and Snowflake setup scripts
- `docs/` – design, setup, specs and plans

## Rebuilding the definitions or the index

Only needed if you change the source definitions or the embedding model:
see `docs/running_the_agent.md` and `census_compass/semantic/`. Changing the embedding
model requires rebuilding and recommitting `data/index/`.
