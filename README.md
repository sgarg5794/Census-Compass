# Census Compass

A conversational agent for exploring US Census data in Snowflake, with clear
source periods, uncertainty, and limitations.

## Status
Project scaffold only. The source-derived semantic catalog and extraction pipeline are implemented.
The agent, tools, embeddings, and session persistence are not implemented yet. No model or embedding provider
is selected automatically. The Streamlit page is a setup placeholder.

## Stack
Google ADK, LiteLLM, Claude Sonnet (model ID to select), LanceDB, Snowflake.
Internal Python tools; no MCP server. Streamlit is an optional UI dependency.

## Local setup
Use Python 3.12:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev,ui,local]'
cp .env.example .env
```

Set actual credentials only in local secret storage or the ignored `.env` file.
The existing local Snowflake helper uses macOS Keychain. See
`docs/snowflake_connection.md`. No passwords or API keys are copied into the repo.

Exact versions from the verified setup are stored in `requirements.lock.txt`.
To reproduce that environment on the same Python/platform combination:

```sh
python -m pip install -r requirements.lock.txt
python -m pip install --no-deps -e .
```

## Commands
```sh
ruff check .
python -m compileall -q census_compass scripts ui
streamlit run ui/app.py
```

Choose `.venv/bin/python` as the PyCharm interpreter.
`adk web` becomes usable once `root_agent` and the tool contracts are implemented.
Catalog transformation tests run with `python -m pytest`.

## Layout
- `census_compass/`: agent, internal tools, semantic retrieval, database access.
- `data/tables/`: individual table YAML schemas and metadata coverage reports.
- `scripts/`: offline extraction/index builds and local Snowflake exploration.
- `ui/`: public chat interface scaffold.
- `evaluations/`: scenarios for future agent evaluation.
- `docs/`: agreed direction, capabilities, setup instructions.
- `data/metadata/` and `data/index/`: raw extraction and LanceDB files, ignored by Git.

Changing indexed descriptions requires explicit reindexing. Changing the embedding
model requires rebuilding vectors. Switching the chat model does not.

## Extract semantic definitions

```sh
.venv/bin/python -m scripts.extract_metadata
.venv/bin/python -m scripts.build_catalog
```

Individual table schemas and missing-column reports are saved under `data/tables/`. The builder
preserves source definitions in the approved dimensions/measures format. Population
count aggregation permissions follow the reviewed example; other measures require
individual review. It does not invent missing field meanings. See `data/tables/README.md` for output details.

## Semantic schema v1
Each table file in `data/tables/` contains dimensions, measures with nested margins
of error, verified relationships, constraints, and unresolved columns. Physical
filenames remain stable; a small lookup index also supports logical model names.
No aggregation defaults are defined. See `data/tables/README.md` for the rule scope.
