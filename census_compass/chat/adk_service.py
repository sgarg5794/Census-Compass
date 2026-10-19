"""Visitor-isolated adapter for an application-owned, scope-gated agent runtime."""

from collections.abc import Iterator
from typing import Protocol
from uuid import uuid4

from pydantic import ValidationError

from census_compass.chat.contracts import (
    ChatEvent,
    ChatRequest,
    ErrorEvent,
    ResultEvent,
    StatusEvent,
    parse_chat_event,
)
from census_compass.chat.service import ChatService, UnavailableChatService


class AgentRuntime(Protocol):
    """Trusted backend that emits only user-facing events and verified evidence.

    The runtime owns ADK resources and must preserve context for a repeated
    ``(visitor_id, conversation_id)`` pair. Its ``executed_sql`` evidence must
    come from completed, validated query execution, never a proposed tool call.
    """

    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]: ...

    def close(self) -> None: ...


class AdkChatService:
    """Map browser-owned chats to opaque, visitor-isolated runtime sessions."""

    _ERROR = "Census Compass couldn't complete that request. Please try again."
    _ACCESS_ERROR = "This conversation is unavailable."

    def __init__(self, runtime: AgentRuntime):
        self._runtime = runtime
        self._conversation_owner: dict[str, str] = {}
        self._runtime_conversation: dict[str, str] = {}
        self._runtime_visitor: dict[str, str] = {}
        self._closed = False

    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
        if self._closed:
            yield ErrorEvent(message=self._ERROR)
            return

        owner = self._conversation_owner.get(request.conversation_id)
        if owner is not None and owner != request.visitor_id:
            yield ErrorEvent(message=self._ACCESS_ERROR)
            return

        if owner is None:
            self._conversation_owner[request.conversation_id] = request.visitor_id
            self._runtime_conversation[request.conversation_id] = uuid4().hex
        if request.visitor_id not in self._runtime_visitor:
            self._runtime_visitor[request.visitor_id] = uuid4().hex

        runtime_request = request.model_copy(update={
            "visitor_id": self._runtime_visitor[request.visitor_id],
            "conversation_id": self._runtime_conversation[request.conversation_id],
        })

        try:
            for raw_event in self._runtime.stream_turn(runtime_request):
                try:
                    event = parse_chat_event(raw_event)
                except (ValidationError, ValueError, TypeError):
                    continue
                if isinstance(event, StatusEvent):
                    yield event
                elif isinstance(event, ResultEvent):
                    yield event
                    return
                elif isinstance(event, ErrorEvent):
                    yield ErrorEvent(message=self._ERROR)
                    return
        except Exception:
            yield ErrorEvent(message=self._ERROR)
            return

        yield ErrorEvent(message=self._ERROR)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._runtime.close()


def create_agent_runtime() -> AgentRuntime:
    """Import the live runtime only when the chat service is first requested."""
    from census_compass.chat.adk_runtime import create_agent_runtime as build_runtime

    return build_runtime()


def create_chat_service(runtime: AgentRuntime | None = None) -> ChatService:
    """Build the live service lazily, or fail closed if its runtime cannot start."""
    if runtime is None:
        try:
            runtime = create_agent_runtime()
        except Exception:
            return UnavailableChatService()
    return AdkChatService(runtime)
