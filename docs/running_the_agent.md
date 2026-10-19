# Run the agent

Census Compass runs as a Google ADK agent backed by Claude through LiteLLM.

## Prerequisites

1. **Generated definitions and search index.** `definitions/generated/` (semantic
   definitions) and `data/index/` (the LanceDB search index) are committed, so a
   fresh checkout already has them. Rebuild only after changing the sources or the
   embedding model.
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

## Streamlit chat UI

The Streamlit UI follows the approved design in `docs/specs/2026-10-18-streamlit-ui-design.md`.

### Run

From the project root:

```bash
.venv/bin/python -m streamlit run ui/app.py
```

Open the URL printed by Streamlit. This launches the local development app; it does not publish it.

### Experience

The sidebar contains only New chat and conversation history. The welcome screen offers four data questions. Click an example or enter a question, then use follow-ups in the same conversation. New chat starts an independent conversation; selecting an earlier entry restores its transcript.

Messages use a Python chat-service boundary. The interface displays user-facing status updates and completed answers, result tables and query/source details when the service supplies them. Internal reasoning, prompts and retrieved schemas are never displayed. Illustrative responses from the design preview are not production fallback answers.

### Session lifetime

Conversation history is scoped to the active Streamlit browser session. A reload, disconnect or server restart can lose it. No login, cross-device identity or durable conversation storage is implemented in this UI increment. The permanent memory backend and retention policy remain separate decisions.

Anonymous visitor and conversation identifiers are generated on the server. Do not expose arbitrary session selection from a URL or reuse another visitor's mutable state. One message runs at a time; the UI prevents chat switching and repeated submissions while a request is active.

### Runtime factory and backend readiness

`census_compass.chat.adk_service.create_chat_service()` lazily creates `CensusAgentRuntime`, which wraps the existing `root_agent` in an ADK `InMemoryRunner`. One runtime belongs to each Streamlit browser session. It keeps a persistent event loop on a dedicated thread and reuses an ADK session for each opaque visitor/conversation pair. Conversation state is in memory and is lost when the browser session or server ends. The adapter validates user-facing events and closes the runtime when Streamlit releases that browser session; cleanup on a network disconnect is eventual, not immediate.

The installed ADK interfaces inspected on 2026-10-18 are `Runner.run(*, user_id, session_id, new_message, state_delta=None, run_config=None)` and `Runner.run_async(*, user_id, session_id, invocation_id=None, new_message=None, state_delta=None, run_config=None, ...)`. `InMemorySessionService.create_session(*, app_name, user_id, state=None, session_id=None)` and `get_session(*, app_name, user_id, session_id, config=None)` provide session access. The implementation should check whether these methods are awaitable before choosing its loop/lifecycle strategy. Do not repeatedly run a persistent async client on newly created event loops.

Message screening happens inside the agent (see `docs/running_the_agent.md`, "Guardrails and the glossary"), so the Streamlit UI, `adk web` and any API are protected the same way. The UI forwards every message to the agent and shows its reply; there is no separate UI-side gate. The dataset agent still relies on its existing tools for geography/year clarification and validated Snowflake queries.


The final persistent-memory choice, Snowflake service-user authentication, hosting and public usage limits are separate tasks.

### Verify

```bash
.venv/bin/python -m pytest tests/unit/test_chat_contracts.py tests/unit/test_chat_state.py tests/unit/test_chat_adk_service.py tests/ui/test_app.py -q
```

The UI and adapter tests inject test-only services/runtimes. They do not need model API keys or Snowflake access. Also review the running page at desktop and narrow widths, with two independent browser sessions, before deployment.
