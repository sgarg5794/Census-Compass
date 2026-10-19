"""Pure, browser-session-local transitions for Census Compass conversations.

The Streamlit page owns a separate ``ChatState`` in each browser session's
``st.session_state['chat_state']``. This module has no process-global state.
"""

from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4

from census_compass.chat.contracts import ChatRequest, ChatResult


@dataclass
class ChatMessage:
    id: str
    role: Literal["user", "assistant"]
    text: str
    result: ChatResult | None = None


@dataclass
class Conversation:
    id: str
    title: str = "New chat"
    messages: list[ChatMessage] = field(default_factory=list)
    processed_request_ids: set[str] = field(default_factory=set)


@dataclass
class ChatState:
    visitor_id: str
    conversations: dict[str, Conversation]
    active_id: str
    pending_request: ChatRequest | None = None


def _new_id() -> str:
    return uuid4().hex


def new_state() -> ChatState:
    """Create an independent visitor and an initially blank conversation."""
    conversation_id = _new_id()
    return ChatState(
        visitor_id=_new_id(),
        conversations={conversation_id: Conversation(id=conversation_id)},
        active_id=conversation_id,
    )


def new_conversation(state: ChatState) -> str:
    """Start another blank conversation without removing prior history."""
    if state.pending_request is not None:
        raise ValueError("Cannot start a conversation while a turn is pending")
    conversation_id = _new_id()
    state.conversations[conversation_id] = Conversation(id=conversation_id)
    state.active_id = conversation_id
    return conversation_id


def select_conversation(state: ChatState, conversation_id: str) -> None:
    """Restore a conversation in this visitor's state."""
    if state.pending_request is not None:
        raise ValueError("Cannot switch conversations while a turn is pending")
    if conversation_id not in state.conversations:
        raise ValueError("Unknown conversation")
    state.active_id = conversation_id


def begin_turn(state: ChatState, text: str) -> ChatRequest:
    """Record one submitted question and reserve the sole in-flight turn."""
    if state.pending_request is not None:
        raise ValueError("A turn is already pending")
    if not text.strip():
        raise ValueError("Question cannot be blank")

    conversation = state.conversations[state.active_id]
    request = ChatRequest(
        visitor_id=state.visitor_id,
        conversation_id=conversation.id,
        request_id=_new_id(),
        text=text,
    )
    if not conversation.messages:
        conversation.title = " ".join(text.split())[:60].rstrip()
    conversation.messages.append(ChatMessage(id=_new_id(), role="user", text=text))
    state.pending_request = request
    return request


def _conversation_for_completion(state: ChatState, request_id: str) -> Conversation | None:
    for conversation in state.conversations.values():
        if request_id in conversation.processed_request_ids:
            return None
    request = state.pending_request
    if request is None or request.request_id != request_id:
        raise ValueError("Unknown pending request")
    return state.conversations[request.conversation_id]


def finish_turn(state: ChatState, request_id: str, result: ChatResult) -> None:
    """Append the answer once and release the pending turn."""
    conversation = _conversation_for_completion(state, request_id)
    if conversation is None:
        return
    conversation.messages.append(
        ChatMessage(id=_new_id(), role="assistant", text=result.answer, result=result)
    )
    conversation.processed_request_ids.add(request_id)
    state.pending_request = None


def fail_turn(state: ChatState, request_id: str, message: str) -> None:
    """Record a safe error once and leave the chat ready for another turn."""
    conversation = _conversation_for_completion(state, request_id)
    if conversation is None:
        return
    conversation.messages.append(ChatMessage(id=_new_id(), role="assistant", text=message))
    conversation.processed_request_ids.add(request_id)
    state.pending_request = None
