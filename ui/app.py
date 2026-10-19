"""Census Compass public chat page."""

import weakref

import streamlit as st

from census_compass.chat.adk_service import create_chat_service
from census_compass.chat.contracts import ErrorEvent, ResultEvent, StatusEvent
from census_compass.chat.service import ChatService, UnavailableChatService
from census_compass.chat.state import (
    begin_turn,
    fail_turn,
    finish_turn,
    new_conversation,
    new_state,
    select_conversation,
)
from census_compass.warmup import start_warm_up
from ui.components import render_message, render_sidebar, render_welcome


@st.cache_resource
def _warm_up_once() -> bool:
    """Once per server process: load the embedding model before the first question."""
    start_warm_up()
    return True


STATUS_LABELS = {
    "understanding": "Understanding your question",
    "finding_data": "Finding relevant Census data",
    "querying": "Querying Census data",
    "preparing_answer": "Preparing the answer",
}

SAFE_FAILURE = "I couldn't complete that request. Please try again."


def _session_service() -> ChatService:
    """Keep one service per browser session, including its agent session map."""
    service = st.session_state.get("chat_service")
    if service is None:
        service = create_chat_service()
        st.session_state["chat_service"] = service
    return service


def _consume_turn(service: ChatService, request) -> None:
    state = st.session_state["chat_state"]
    terminal_event = None
    with st.status("Working on your question…") as status:
        try:
            for event in service.stream_turn(request):
                if isinstance(event, StatusEvent):
                    if terminal_event is not None:
                        raise ValueError("Status after terminal event")
                    status.update(label=STATUS_LABELS[event.stage])
                elif isinstance(event, (ResultEvent, ErrorEvent)):
                    if terminal_event is not None:
                        raise ValueError("More than one terminal event")
                    terminal_event = event
                else:
                    raise ValueError("Unknown chat event")
            if isinstance(terminal_event, ResultEvent):
                finish_turn(state, request.request_id, terminal_event.result)
            elif isinstance(terminal_event, ErrorEvent):
                message = terminal_event.message if isinstance(service, UnavailableChatService) else SAFE_FAILURE
                fail_turn(state, request.request_id, message)
            else:
                fail_turn(state, request.request_id, SAFE_FAILURE)
        except Exception:
            fail_turn(state, request.request_id, SAFE_FAILURE)
        finally:
            status.update(label="Request complete", state="complete")


def render_app(service: ChatService) -> None:
    """Render one browser session with an injected chat service."""
    st.set_page_config(page_title="Census Compass", page_icon="🧭", layout="centered")
    state = st.session_state.get("chat_state")
    if state is None:
        state = new_state()
        st.session_state["chat_state"] = state
    if "chat_service" not in st.session_state:
        st.session_state["chat_service"] = service
    service = st.session_state["chat_service"]
    if "service_finalizer" not in st.session_state:
        # The state is owned by this Streamlit browser session. When it is
        # collected, release the matching service and runtime resources.
        st.session_state["service_finalizer"] = weakref.finalize(state, service.close)

    pending = state.pending_request
    dispatch_id = st.session_state.get("dispatch_request_id")
    if pending is not None and pending.request_id != dispatch_id:
        fail_turn(state, pending.request_id, SAFE_FAILURE)
        pending = None
    elif pending is None:
        st.session_state.pop("dispatch_request_id", None)

    action = render_sidebar(state)
    if action is not None:
        if action[0] == "new":
            new_conversation(state)
        else:
            select_conversation(state, action[1])

    st.title("Census Compass")
    st.caption("Census data, explained")
    conversation = state.conversations[state.active_id]
    example = None
    if conversation.messages:
        for message in conversation.messages:
            render_message(message)
    else:
        example = render_welcome()

    typed = st.chat_input(
        "Ask a question about Census data…", disabled=state.pending_request is not None
    )
    if pending is not None:
        # The pending state was rendered with disabled controls before the
        # first service event. Clear this marker before entering external code
        # so an interrupted rerun cannot dispatch the request twice.
        st.session_state.pop("dispatch_request_id", None)
        _consume_turn(service, pending)
        st.rerun()

    question = example or typed
    if question and question.strip():
        request = begin_turn(state, question)
        st.session_state["dispatch_request_id"] = request.request_id
        st.rerun()


if __name__ == "__main__":
    _warm_up_once()
    render_app(_session_service())
