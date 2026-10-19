# Census Compass Streamlit UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, or superpowers:subagent-driven-development if the user explicitly chooses delegation. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Streamlit chat interface with isolated conversation history and a defined connection to the Census agent.

**Architecture:** Keep Streamlit rendering separate from conversation state and the agent-facing service. The UI consumes typed, user-facing events from a synchronous Python service; an adapter owns ADK interaction and maps tool outcomes to safe presentation. Begin with injected fixtures for tests, then connect to the live runtime when its agent factory and pre-loop scope gate are available.

**Tech Stack:** Existing Python 3.12 environment, Streamlit, Pydantic 2, Google ADK, pytest and Streamlit AppTest. Reuse existing dependencies; no React, FastAPI or new database dependency.

**Spec:** `docs/superpowers/specs/2026-10-18-streamlit-ui-design.md`. Approved preview: `docs/superpowers/specs/census-compass-ui-preview.html`.

## Global Constraints

- Sidebar: New chat and conversation history only.
- Welcome prompts are data questions; use the four exact questions in the spec.
- Public UI requires no login; identities and conversation ownership are assigned server-side.
- No dataset explorer, schema browser, developer console or raw model reasoning.
- No mock Census numbers appear in production; fixtures are test-only and must never become a silent fallback.
- Use browser-session-scoped history for this increment; durable memory and retention remain a separate decision.
- Scope classification runs before ReAct; classifier implementation and hosting are excluded here. Public live operation is blocked until the gate exists.
- Reuse the existing 60-second backend request budget; do not independently reset it for UI stages or tool calls.
- Preserve existing query safety, row limits and completeness information. Never execute SQL from the UI.
- Do not modify unrelated existing changes, including README edits or `census_compass/prompts/Prompt.py`.

## Review Focus

1. Streamlit reruns after expander/sidebar interaction must not repeat a model request or SQL execution: Task 2 and Task 4 tests.
2. Two anonymous visitors must not discover or reuse each other's chat IDs or model context: Task 2 and Task 5 tests.
3. A failed request or missing backend must release the busy state and leave a usable composer: Task 3 and Task 4 tests.
4. Query results can contain long text, nulls, Decimal values or hostile markup; render safely and preserve meaningful values: Task 3 and Task 4 tests.
5. New chat and conversation switching must restore the correct agent session and pending clarification: Task 2 and Task 5 tests.

## Current project and file ownership

Project root: `/Users/sahilgarg/Desktop/Census-Compass`.

`ui/app.py`, `census_compass/agent.py` and `census_compass/sessions.py` are placeholders at planning time. Existing `census_compass/contracts.py` provides `Outcome`, `QueryResult`, `Completeness` and `Period`; reuse these types without rewriting the agent plan. Query, validation and resolver tools already exist. Recheck agent entry points immediately before Task 5 because other agent work may land in parallel.

| File | Responsibility |
|---|---|
| `census_compass/chat/contracts.py` | UI-facing requests, messages, final results and safe events |
| `census_compass/chat/state.py` | Conversation creation, selection, submission IDs and transcript updates |
| `census_compass/chat/service.py` | ChatService protocol and service-unavailable implementation |
| `census_compass/chat/adk_service.py` | Runtime adapter, visitor ownership and safe event mapping |
| `ui/app.py` | Page entry point and Streamlit event flow |
| `ui/components.py` | Welcome, sidebar, message, result and evidence rendering |
| `.streamlit/config.toml` | Restrained native Streamlit theme matching the preview |
| `tests/fixtures/chat.py` | Scripted service and deterministic event fixtures; never production-imported |
| `tests/unit/test_chat_*.py` | Contract, state and adapter behavior |
| `tests/ui/test_app.py` | Streamlit AppTest interactions |
| `docs/ui.md` | Run command, runtime configuration and known session limits |

Create `__init__.py` in new packages as needed. Do not change `census_compass/agent.py`, `sessions.py`, model prompts or SQL tools solely to make the page run. Any necessary runtime change belongs to agent integration and must be identified explicitly.

## Task 1: Define the chat boundary

**Files:** Create `census_compass/chat/{__init__,contracts,service}.py`; test `tests/unit/test_chat_contracts.py`.

**Interfaces:**
- `ChatRequest`: `visitor_id: str`, `conversation_id: str`, `request_id: str`, `text: str`.
- `Evidence`: `executed_sql: str | None`, `source_label: str | None`, `period_label: str | None`, `limitations: list[str]`; all values supplied by the backend, not inferred by the UI.
- `ChatResult`: `outcome: Outcome`, `answer: str`, `rows: list[dict]`, `evidence: list[Evidence]`, `completeness: list[Completeness]`, `truncated: bool`.
- `ChatEvent`: discriminated union of `StatusEvent(kind='status', stage=...)`, `ResultEvent(kind='result', result: ChatResult)`, and `ErrorEvent(kind='error', message: str)`.
- Status stages: `understanding`, `finding_data`, `querying`, `preparing_answer`; UI maps these to fixed safe labels. Only emit a stage after the runtime actually reaches it.
- `ChatService` protocol: `stream_turn(request: ChatRequest) -> Iterator[ChatEvent]`, `close() -> None`.
- `UnavailableChatService`: returns one safe error, with no model or database call.

- [ ] Write tests: blank/whitespace requests fail validation; unknown event stages fail; a final result preserves nulls and completeness; unavailable service emits exactly one error and no fabricated result.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_chat_contracts.py -q`; confirm the new tests fail for missing interfaces.
- [ ] Implement typed contracts and protocol. Existing `Outcome.UNSUPPORTED` can represent an off-topic result without changing global agent contracts.
- [ ] Rerun tests; expect all pass. Commit only the task's files: `feat: define chat UI service contracts`.

## Task 2: Isolated conversations and rerun-safe state

**Files:** Create `census_compass/chat/state.py`; test `tests/unit/test_chat_state.py`.

**Interfaces:**
- `ChatMessage`: `id`, `role` (`user` or `assistant`), `text`, optional `result: ChatResult`.
- `Conversation`: `id`, `title`, `messages: list[ChatMessage]`, `processed_request_ids: set[str]`.
- `ChatState`: `visitor_id`, `conversations: dict[str, Conversation]`, `active_id: str`, `pending_request: ChatRequest | None`.
- `new_state() -> ChatState`: server-generated random visitor ID and one blank conversation.
- `new_conversation(state: ChatState) -> str`; `select_conversation(state: ChatState, conversation_id: str) -> None`.
- `begin_turn(state: ChatState, text: str) -> ChatRequest`: creates one request ID, appends one user message and establishes pending state; refuses a concurrent submission.
- `finish_turn(state: ChatState, request_id: str, result: ChatResult) -> None`; `fail_turn(state: ChatState, request_id: str, message: str) -> None`: idempotent completion, append to the request's conversation and release pending state.

- [ ] Write tests for independent visitor IDs and conversation objects; two chats restore their own messages; first question becomes the title (maximum 60 characters); repeated completion produces one assistant message; a completed request cannot become pending again through rendering; switching or starting a chat while busy is refused; unknown conversation IDs are rejected.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_chat_state.py -q`; expect failure before implementation.
- [ ] Implement pure state transitions. Titles use trimmed first-question text, replacing newlines with spaces; do not call a model. Exclude empty drafts from history. Store the resulting `ChatState` in `st.session_state['chat_state']`, never a global transcript or shared cache.
- [ ] Rerun tests; expect pass. Commit `feat: add isolated conversation state`.

## Task 3: Build the approved screen and response components

**Files:** Modify `ui/app.py`; create `ui/__init__.py`, `ui/components.py`, `.streamlit/config.toml`, `tests/fixtures/chat.py`, `tests/ui/test_app.py`.

**Interfaces:**
- `render_welcome() -> str | None`: returns the clicked example's exact text.
- `render_sidebar(state: ChatState) -> tuple[str, str | None] | None`: returns `('new', None)` or `('select', id)`; no extra sidebar navigation.
- `render_message(message: ChatMessage) -> None`: user/assistant Markdown; structured rows through `st.dataframe`; evidence through `st.expander` and `st.code`.
- `render_app(service: ChatService) -> None`: dependency-injected entry point for tests; bootstrap separately selects the live/unavailable service.

- [ ] Write AppTest cases asserting the title, four exact example buttons and chat input exist; sidebar contains only New chat/history; there is no schema/dataset navigation; selecting an example yields its exact text once; an answer with no evidence has no query expander.
- [ ] Run `.venv/bin/python -m pytest tests/ui/test_app.py -q`; confirm expected failures.
- [ ] Implement native `st.sidebar`, `st.button`, `st.chat_message`, `st.chat_input`, `st.status`, `st.dataframe` and `st.expander`. Use two welcome columns on desktop with natural reflow. Use minimal native theming, not copied mockup DOM/CSS or private Streamlit selectors. Input placeholder: `Ask a question about Census data…`.
- [ ] Add rendering cases: null remains missing, not zero; Decimal values remain meaningful; long text wraps/scrolls in a native table; SQL uses `st.code(language='sql')`; no `unsafe_allow_html=True` for model/database content. Truncation and material limitations remain visible in the answer, not only in an expander.
- [ ] Rerun AppTest and relevant unit tests; expect pass. Commit `feat: build Census Compass Streamlit interface`.

## Task 4: Wire submissions, progress and terminal outcomes

**Files:** Modify `ui/app.py`, `ui/components.py`; expand `tests/ui/test_app.py` and `tests/fixtures/chat.py`.

**Interfaces:** Consumes Tasks 1–3. One submission calls `ChatService.stream_turn(request)` once. A status updates `st.status`; a result completes the pending turn; an error maps to a safe assistant response and completes it. A stream ending with no terminal event is an error. One terminal event is allowed; do not display partial internal output.

- [ ] Add scripted-service tests for success with a table/evidence, clarification, unsupported result, off-topic redirect, timeout, database failure, model failure, missing backend and stream ending unexpectedly. Assert no fixture returns fabricated production answers.
- [ ] Add a counting fixture: submit once, rerun the app and select history; service invocation count remains one. Check examples and typed submissions share the same path. Check busy state disables composer, New chat and history selection.
- [ ] Run targeted AppTest cases; expect new assertions to fail.
- [ ] Implement consumption with `try/finally` cleanup. Never auto-retry failed turns or replay a pending turn after an interrupted rerun. A recovered interrupted turn becomes a safe failure message. Result ownership is determined from the pending request, not the currently rendered conversation. Do not sleep or invent progress stages in production.
- [ ] Run all UI/state tests; expect pass. Commit `feat: connect chat interaction and safe progress states`.

## Task 5: Connect the UI service to the live ADK runtime

**Files:** Create `census_compass/chat/adk_service.py`; test `tests/unit/test_chat_adk_service.py`; update UI bootstrap only. Read `census_compass/agent.py` and `sessions.py` again before implementation.

**Interfaces:**
- Adapter dependency `AgentRuntime` protocol: `stream_turn(request: ChatRequest) -> Iterator[ChatEvent]`, `close() -> None`. The implementation owns ADK Runner/SessionService and the pre-ReAct scope check. Prefer adapting an existing application-level runtime instead of creating a second agent stack.
- `AdkChatService(runtime: AgentRuntime)`: enforces visitor ownership and maps safe runtime events to UI events. Reuse the same runtime session for the same `(visitor_id, conversation_id)` pair; a new conversation gets a new session. Never populate a different chat by replaying its transcript.
- `create_chat_service() -> ChatService`: lazy live initialization; if required agent factory or scope gate is absent, return UnavailableChatService with a clear safe message. No model/database calls at module import.

- [ ] Pin the installed ADK Runner and SessionService interfaces at execution time. Record the concrete live runtime factory in `docs/ui.md`; if no factory exists, identify it as an agent-work dependency, not an invitation to recreate the ReAct agent in UI code.
- [ ] Write fake-runtime tests: same chat retains session; New chat gets a different one; clarification followed by a reply reaches the same runtime context; foreign visitor cannot access a chat; missing agent/scope gate yields safe unavailability; prompts, reasoning, secrets and raw tool events never reach the renderer; SQL appears only when the backend identifies it as executed evidence.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_chat_adk_service.py -q`; verify failure before implementation.
- [ ] Implement the adapter and bootstrap. Runtime resources must be browser-session scoped with a defined close lifecycle or be demonstrably safe to share without mutable user state. Use ADK's supported Runner integration rather than repeatedly wrapping a persistent async client in short-lived `asyncio.run` loops. Do not change global Snowflake connection concurrency as part of UI work; verify separately before public deployment.
- [ ] Rerun adapter and UI tests; expect pass. If runtime dependencies exist, manually submit a Census question and follow-up through the live UI. If absent, verify honest unavailability and report that live wiring is incomplete; do not mark this task fully complete.
- [ ] Commit `feat: integrate Streamlit with Census chat runtime` only for implemented behavior.

## Task 6: Verify the experience and document the handoff

**Files:** Create `docs/ui.md`; no unrelated README changes.

- [ ] Document launch from project root: `.venv/bin/python -m streamlit run ui/app.py`. Describe the service factory, test injection, runtime prerequisites and browser-session lifetime. Explicitly state that service-user provisioning, permanent memory, guardrail implementation and hosting are separate work.
- [ ] Run `.venv/bin/python -m pytest tests/unit/test_chat_contracts.py tests/unit/test_chat_state.py tests/unit/test_chat_adk_service.py tests/ui/test_app.py -q`; expect all implemented tests pass without network, credentials or model calls.
- [ ] Run `.venv/bin/ruff check census_compass/chat ui tests/ui tests/unit/test_chat_contracts.py tests/unit/test_chat_state.py tests/unit/test_chat_adk_service.py` and `git diff --check`; expect clean. Run existing affected tests if any shared agent contract changed; avoid unrelated dependency upgrades.
- [ ] Inspect the actual rendered app at 1280 px and 390 px, light/dark appearance, keyboard submission, long tables, sidebar selection and narrow chat input. Verify no clipped send control or overlapping composer. Confirm the actual app preserves the approved layout while using Streamlit-native behavior.
- [ ] Confirm sample numbers exist only in tests/reference preview, not application fallbacks. Check that raw schema, retrieved YAML and internal reasoning never appear.
- [ ] Commit only owned documentation/verification changes: `docs: document Streamlit UI setup and session limits`.

## Completion and later work

## Execution status (2026-10-18)

- Tasks 1–4 are implemented and covered by unit and Streamlit AppTest checks.
- Task 5's typed ADK runtime adapter and safe unavailable bootstrap are implemented and tested with injected runtimes. The live-runtime acceptance remains open: the application-level runtime with the required pre-ReAct scope gate is not available, so production UI requests return a safe unavailable response.
- Task 6 documentation and verification are complete for the available UI boundary. The full project suite passes (543 passed, 14 skipped); targeted Ruff and `git diff --check` pass. Desktop/mobile browser checks confirmed history restore, independent browser-session state, and narrow-screen question wrapping.
- Final branch review approved with no findings. Live ADK/Snowflake behavior, hosting, service-user authentication, durable memory, and guardrail implementation remain separate work.

UI-only acceptance: Tasks 1–4 and Task 6 pass with an injected fixture, and production displays honest unavailability if agent dependencies are missing. Live integration acceptance additionally requires Task 5 and an actual data question/follow-up smoke test. Neither milestone means the public deployment is ready: the scope gate, service-user authentication, deployment limits and final session-memory design remain required deployment work.

The approved HTML is a visual reference. Do not ship its scripted responses or JavaScript as the Streamlit application.

## References checked during planning

- Streamlit chat elements: https://docs.streamlit.io/develop/api-reference/chat
- Streamlit AppTest: https://docs.streamlit.io/develop/api-reference/app-testing/st.testing.v1.apptest
- Streamlit Session State lifecycle: https://docs.streamlit.io/develop/api-reference/caching-and-state/st.session_state
- ADK runner integration: https://github.com/google/adk-python/blob/main/docs/guides/runners/runner/index.md

## Self-review

- All approved screens and both user corrections are assigned to Tasks 2–4.
- The five Review Focus conditions each have named behavioral assertions in their owning tasks.
- Shared request/result/event signatures are defined before consumption; global agent contracts are reused.
- No implementation, live SQL, new service user or deployment is authorized by this plan-writing step.
- Agent entry points remain an explicit dependency; permanent session storage is not silently selected.
