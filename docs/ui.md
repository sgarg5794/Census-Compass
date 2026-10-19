# Census Compass UI

The Streamlit UI follows the approved design in `docs/superpowers/specs/2026-10-18-streamlit-ui-design.md`.

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

`census_compass.chat.adk_service.create_chat_service(runtime=None)` returns a safe unavailable service by default. An application-owned `AgentRuntime` can be explicitly injected through `create_chat_service(runtime)`. The adapter maps each visitor/conversation pair to opaque runtime identifiers, forwards only the current question, accepts only validated user-facing events and closes the injected runtime once when its owner releases it. The Streamlit bootstrap owns one service per browser session, not a process-global transcript or user-state cache. It stores that service in the browser-scoped Session State and registers a weak-reference finalizer on the browser's ChatState. When Streamlit releases that state and it is garbage-collected, the finalizer closes the corresponding service/runtime. This is eventual cleanup, not a promise of immediate cleanup on a network disconnect; graceful process shutdown and the future durable-runtime lifecycle still need deployment-level design.

The installed ADK interfaces inspected on 2026-10-18 are `Runner.run(*, user_id, session_id, new_message, state_delta=None, run_config=None)` and `Runner.run_async(*, user_id, session_id, invocation_id=None, new_message=None, state_delta=None, run_config=None, ...)`. `InMemorySessionService.create_session(*, app_name, user_id, state=None, session_id=None)` and `get_session(*, app_name, user_id, session_id, config=None)` provide session access. The implementation should check whether these methods are awaitable before choosing its loop/lifecycle strategy. Do not repeatedly run a persistent async client on newly created event loops.

`census_compass/agent.py` now defines `root_agent`, but `census_compass/sessions.py` is still a placeholder and there is no application-level scope-gated runtime factory. The UI adapter is tested with an injected runtime; this is not a live ADK/Snowflake connection. To enable one, agent work must supply Runner/SessionService lifecycle, the pre-ReAct scope check, the shared request deadline and safe events containing only verified execution evidence.


The UI must not bypass the pre-ReAct scope check. The service factory must report safe unavailability when an agent runtime with that scope gate is missing. A working UI and fixture-based tests do not mean that public live operation is ready.

Guardrail implementation, the final persistent-memory choice, Snowflake service-user authentication, hosting and public usage limits are separate tasks. Local personal Keychain credentials do not become deployment credentials automatically.

## Verify

```bash
.venv/bin/python -m pytest tests/unit/test_chat_contracts.py tests/unit/test_chat_state.py tests/unit/test_chat_adk_service.py tests/ui/test_app.py -q
```

The UI and adapter tests inject test-only services/runtimes. They do not need model API keys or Snowflake access. Also review the running page at desktop and narrow widths, with two independent browser sessions, before deployment.
