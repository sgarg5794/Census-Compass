"""Live, browser-session-owned ADK runtime for the Streamlit chat service."""

import asyncio
import logging
import queue
import threading
from collections.abc import Iterator

from google.adk.runners import InMemoryRunner
from google.genai import types

from census_compass.agent import root_agent
from census_compass.chat.contracts import (
    ChatRequest,
    ChatResult,
    ErrorEvent,
    ResultEvent,
    StatusEvent,
)
from census_compass.chat.scope_gate import classify_dataset_question
from census_compass.contracts import Outcome

log = logging.getLogger(__name__)
_STOP = object()


class CensusAgentRuntime:
    """Run the existing Census agent with isolated in-memory ADK sessions.

    Each Streamlit browser session owns one instance. Session data is retained only for
    that instance's lifetime and is never shared between visitors.
    """

    def __init__(self, runner: InMemoryRunner | None = None):
        self._runner = runner or InMemoryRunner(agent=root_agent, app_name="census_compass")
        self._sessions: dict[tuple[str, str], str] = {}
        self._dataset_conversations: set[tuple[str, str]] = set()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, name="census-adk-runtime", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stream_turn(self, request: ChatRequest) -> Iterator:
        if self._closed:
            yield ErrorEvent(message="Census Compass couldn't complete that request. Please try again.")
            return

        key = (request.visitor_id, request.conversation_id)
        route = classify_dataset_question(
            request.text, has_dataset_context=key in self._dataset_conversations
        )
        if route != "dataset":
            yield ResultEvent(result=ChatResult(
                outcome={
                    "clarify": Outcome.CLARIFICATION_NEEDED,
                    "unsupported_general_census": Outcome.UNSUPPORTED,
                }.get(route, Outcome.UNSUPPORTED),
                answer={
                    "clarify": "Please ask a specific question about a Census data measure or geography.",
                    "unsupported_general_census": (
                        "I can answer questions from the connected Census dataset. General Census "
                        "explanations aren't available until approved Census Bureau documents are connected."
                    ),
                    "out_of_scope": "I can help with questions about US Census data. Please ask a Census data question.",
                }[route],
                rows=[], evidence=[], completeness=[], truncated=False,
            ))
            return

        self._dataset_conversations.add(key)
        events: queue.Queue = queue.Queue()
        future = self._submit(self._run_agent_turn(request, events))
        try:
            while True:
                item = events.get()
                if item is _STOP:
                    break
                yield item
            future.result()
        except Exception:
            log.exception("ADK request failed")
            yield ErrorEvent(message="Census Compass couldn't complete that request. Please try again.")

    async def _run_agent_turn(self, request: ChatRequest, events: queue.Queue) -> None:
        key = (request.visitor_id, request.conversation_id)
        try:
            session_id = self._sessions.get(key)
            if session_id is None:
                session = await self._runner.session_service.create_session(
                    app_name="census_compass", user_id=request.visitor_id
                )
                session_id = session.id
                self._sessions[key] = session_id

            events.put(StatusEvent(stage="understanding"))
            content = types.Content(role="user", parts=[types.Part(text=request.text)])
            answer_parts: list[str] = []
            query_started = False
            async for event in self._runner.run_async(
                user_id=request.visitor_id,
                session_id=session_id,
                new_message=content,
            ):
                for part in event.get_function_calls():
                    if part.name == "execute_query" and not query_started:
                        events.put(StatusEvent(stage="querying"))
                        query_started = True
                    elif not query_started:
                        events.put(StatusEvent(stage="finding_data"))
                if event.is_final_response() and event.content:
                    answer_parts.extend(
                        part.text for part in event.content.parts or [] if part.text
                    )

            answer = "\n".join(answer_parts).strip()
            if not answer:
                events.put(ErrorEvent(
                    message="Census Compass couldn't complete that request. Please try again."
                ))
                return
            events.put(ResultEvent(result=ChatResult(
                outcome=Outcome.ANSWERED,
                answer=answer,
                rows=[],
                evidence=[],
                completeness=[],
                truncated=False,
            )))
        except Exception as exc:
            log.warning("ADK request failed: %s", type(exc).__name__)
            events.put(ErrorEvent(
                message="Census Compass couldn't complete that request. Please try again."
            ))
        finally:
            events.put(_STOP)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._submit(self._runner.close()).result(timeout=5)
        except Exception:
            log.warning("ADK runner cleanup did not finish cleanly")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


def create_agent_runtime() -> CensusAgentRuntime:
    """Lazily build the configured Census ReAct agent runtime."""
    return CensusAgentRuntime()
