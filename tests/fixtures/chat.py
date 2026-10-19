"""Scripted boundary service for Streamlit tests only."""

from collections.abc import Iterator

from census_compass.chat.contracts import ChatEvent, ChatRequest


class ScriptedChatService:
    def __init__(self, events: list[ChatEvent] | None = None, failure: Exception | None = None):
        self.events = events or []
        self.failure = failure
        self.requests: list[ChatRequest] = []

    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
        self.requests.append(request)
        yield from self.events
        if self.failure is not None:
            raise self.failure

    def close(self) -> None:
        pass
