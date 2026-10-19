# Run the agent

Census Compass runs as a Google ADK agent backed by Claude through LiteLLM.

## Prerequisites

1. **Generated definitions and search index.** Run the build command so that
   `definitions/generated/` (semantic definitions) and `data/index/` (the LanceDB
   search index) exist.
2. **`.env` in the repository root** containing your Anthropic key:

   ```
   ANTHROPIC_API_KEY=...
   ```

   `.env` is git-ignored. The agent loads it at import (python-dotenv), and LiteLLM
   reads the key from the environment. Optionally set `AGENT_MODEL` to override the
   default model (`anthropic/claude-sonnet-5-5`).
3. **Snowflake credentials in the macOS Keychain** for the configured Snowflake
   user; `execute_query` reads the password from the Keychain when it first
   connects.

## Start the web UI

From the repository root:

```
.venv/bin/adk web
```

Open the printed URL, choose the `census_compass` agent and ask a question, for
example "What is the unemployment rate in Cook County, Illinois?". Each question
has a 60-second budget (`REQUEST_BUDGET_SECONDS`). If the model provider fails,
the agent replies with a short "language model service failed" message instead
of the raw error.

## Guardrails and the glossary

Every message passes a guardrail before the agent runs: a small classifier model
blocks off-topic, inappropriate and manipulation messages with a fixed reply
(no agent LLM or tool call), and routes the rest (data, general Census concept,
small talk, mixed). If the classifier fails or times out the message goes
through to the agent. Settings (environment variables):

| Variable | Default | Meaning |
| --- | --- | --- |
| `GUARDRAIL_MODEL` | `anthropic/claude-haiku-4-5-20251001` | Classifier model |
| `GUARDRAIL_TIMEOUT_SECONDS` | `5` | Classifier time limit (also capped by the remaining question budget) |
| `MAX_INPUT_CHARS` | `2000` | Longer messages get a fixed "too long" reply |
| `GUARDRAIL_CONTEXT_TURNS` | `3` | Earlier turns shown to the classifier |
| `GLOSSARY_PATH` | `definitions/source/census_glossary.yaml` | Reference glossary file |

General Census concept questions ("what is a margin of error?") are answered by
the `lookup_glossary` tool, only from the reviewed glossary file, with the source
title and page named. The file is validated by the unit tests
(`validate_glossary`); edit it, then run `.venv/bin/pytest tests/unit`.
