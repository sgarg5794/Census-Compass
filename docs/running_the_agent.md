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
