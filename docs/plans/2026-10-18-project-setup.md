# Project setup implementation plan

Goal: Create the requested scaffold and install dependencies without implementing
the agent. Spec: docs/design.md. Execution: inline, as explicitly requested.

- [x] Create the agreed package structure, configuration template, catalog envelope,
  draft instructions, evaluation examples, and documentation. Preserve .git and .idea.
- [x] Copy existing dataset notes and the working local Snowflake helper without secrets.
- [x] Create a Python 3.12 virtual environment and install the package with dev, UI,
  and local extras. Save exact resolved versions for reproducibility.
- [x] Verify dependency consistency, package imports, lint, and an offline LanceDB
  persistence/search smoke check. No live model/database calls are needed.

No behavior tests are added for docstring-only placeholders. Agent implementation
and its behavioral tests will follow separately. No commit or push is requested.

## Verification results
- Dependency installation succeeded in the project virtual environment.
- pip check: no broken requirements.
- Ruff: all checks passed after organizing copied helper imports.
- Python compilation passed.
- ADK LlmAgent and LiteLlm imports passed.
- Semantic catalog envelope passed JSON Schema validation.
- Synthetic LanceDB vectors persisted, reopened, and returned the expected match.
- Streamlit AppTest rendered the scaffold without exceptions.
- No live LLM, embedding, or Snowflake calls were made during setup verification.
- No behavioral tests exist yet because the agent and tools remain unimplemented.
