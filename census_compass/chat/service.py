"""Service boundary between the Streamlit UI and chat runtime."""

from collections.abc import Iterator
from typing import Protocol

from census_compass.chat.contracts import ChatEvent, ChatRequest, ErrorEvent


class ChatService(Protocol):
    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
        """Yield user-facing events for one validated request."""
        ...

    def close(self) -> None:
        """Release any service-owned resources."""
        ...


class UnavailableChatService:
    """Safe implementation used when no runtime is configured."""

    _MESSAGE = "Census Compass is temporarily unavailable. Please try again later."

    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
        del request
        yield ErrorEvent(message=self._MESSAGE)

    def close(self) -> None:
        return None
