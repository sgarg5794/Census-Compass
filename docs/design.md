# Census Compass — agreed direction

## Problem
Public, grounded US Census chat with multi-turn context, normal responses within
60 seconds, and off-topic/inappropriate-request guardrails.

## Decisions
- Python with Google ADK and LiteLLM; Claude Sonnet initially, exact model ID pending.
- Internal function tools, not an MCP server.
- Tools: resolve_geography, search_schema, get_schema, execute_query.
- One semantic YAML file per physical table/view, using existing dictionaries plus verified enrichment.
- Search demographic descriptions with table context; keep keys available in the
  catalog without standalone key embeddings. Exact code lookup is supported.
- LanceDB stores generated vectors. Reuse them at startup; explicit rebuilds only.
- Same embedding model for compatible document and query encoding. Model/provider
  selection remains open. No separate index manifest for now.
- Retrieval may repeat as the agent discovers missing information.
- Use existing macOS Keychain authentication for local agent development with
  CENSUS_READER and secondary roles disabled. Service-user/key-pair authentication
  is deferred until deployment; track it in TODO.md.

## Structure
`census_compass/agent.py` owns ADK wiring. `tools/` contains agent-facing wrappers;
`semantic/` owns catalog/retrieval/embeddings; `database/` owns connectivity and
validation. Offline preparation lives in `scripts/`; public UI lives in `ui/`.
Streamlit is installed as an optional UI dependency and currently shows a scaffold.

## Scope of this setup
Create directories, package metadata, dependency environment, documentation, and
module placeholders. Copy the existing exploration helper and dataset notes.
No model calls, metadata extraction, vector generation, public deployment, or
production agent implementation is part of this setup.

## Open decisions
Semantic member schema; exact Sonnet version and endpoint; embedding model;
persistent session backend; public hosting; detailed guardrails and budgets.

## Semantic extraction status
The source-derived catalog and coverage reports are generated under data/tables/.
Raw schemas/dictionaries are saved in ignored data/metadata/. Run the two offline
commands in data/tables/README.md to refresh them. Definitions use same-year
metadata with provenance; gaps remain explicit. Detailed rules for aggregation,
reporting periods, grain, and relationships remain future work.

## Approved semantic schema v1
The user approved docs/examples/population_age_and_sex_2020.yaml as the v1 layout
and requested applying it across all tables. Active definitions use dimensions,
measures, nested uncertainty, relationships, constraints, and unresolved columns.
No default aggregation is allowed. Permissions are conservative outside the
reviewed population counts; source metadata and join coverage are retained.
Physical table filenames remain stable, with logical names resolved by an index.
