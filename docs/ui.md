# Census Compass UI

The Streamlit UI follows the approved design in `docs/specs/2026-10-18-streamlit-ui-design.md`.

## Run

From the project root with the existing UI dependencies installed:

```bash
.venv/bin/python -m streamlit run ui/app.py
```

In an isolated worktree that reuses the main project's virtual environment:

```bash
PYTHONPATH=. /Users/sahilgarg/Desktop/Census-Compass/.venv/bin/python -m streamlit run ui/app.py
```

Open the URL printed by Streamlit. This launches the local development app; it does not publish it.

## Experience

The sidebar contains only New chat and conversation history. The welcome screen offers four data questions. Click an example or enter a question, then use follow-ups in the same conversation. New chat starts an independent conversation; selecting an earlier entry restores its transcript.

Messages use a Python chat-service boundary. The interface displays user-facing status updates and completed answers, result tables and query/source details when the service supplies them. Internal reasoning, prompts and retrieved schemas are never displayed. Illustrative responses from the design preview are not production fallback answers.

## Session lifetime

Conversation history is scoped to the active Streamlit browser session. A reload, disconnect or server restart can lose it. No login, cross-device identity or durable conversation storage is implemented in this UI increment. The permanent memory backend and retention policy remain separate decisions.

Anonymous visitor and conversation identifiers are generated on the server. Do not expose arbitrary session selection from a URL or reuse another visitor's mutable state. One message runs at a time; the UI prevents chat switching and repeated submissions while a request is active.

## Runtime factory and backend readiness

`census_compass.chat.adk_service.create_chat_service()` lazily creates `CensusAgentRuntime`, which wraps the existing `root_agent` in an ADK `InMemoryRunner`. One runtime belongs to each Streamlit browser session. It keeps a persistent event loop on a dedicated thread and reuses an ADK session for each opaque visitor/conversation pair. Conversation state is in memory and is lost when the browser session or server ends. The adapter validates user-facing events and closes the runtime when Streamlit releases that browser session; cleanup on a network disconnect is eventual, not immediate.

The installed ADK interfaces inspected on 2026-10-18 are `Runner.run(*, user_id, session_id, new_message, state_delta=None, run_config=None)` and `Runner.run_async(*, user_id, session_id, invocation_id=None, new_message=None, state_delta=None, run_config=None, ...)`. `InMemorySessionService.create_session(*, app_name, user_id, state=None, session_id=None)` and `get_session(*, app_name, user_id, session_id, config=None)` provide session access. The implementation should check whether these methods are awaitable before choosing its loop/lifecycle strategy. Do not repeatedly run a persistent async client on newly created event loops.

Message screening happens inside the agent (see `docs/running_the_agent.md`, "Guardrails and the glossary"), so the Streamlit UI, `adk web` and any API are protected the same way. The UI forwards every message to the agent and shows its reply; there is no separate UI-side gate. The dataset agent still relies on its existing tools for geography/year clarification and validated Snowflake queries.


The UI must not bypass the pre-ReAct scope check. If the live runtime cannot initialize, the service factory returns the safe unavailable service. A working local UI does not make public live operation ready.

Guardrail implementation, the final persistent-memory choice, Snowflake service-user authentication, hosting and public usage limits are separate tasks. Local personal Keychain credentials do not become deployment credentials automatically.

## Verify

```bash
.venv/bin/python -m pytest tests/unit/test_chat_contracts.py tests/unit/test_chat_state.py tests/unit/test_chat_adk_service.py tests/ui/test_app.py -q
```

The UI and adapter tests inject test-only services/runtimes. They do not need model API keys or Snowflake access. Also review the running page at desktop and narrow widths, with two independent browser sessions, before deployment.
