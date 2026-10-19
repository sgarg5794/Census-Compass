"""Census Compass public chat page."""

import streamlit as st

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
from ui.components import render_message, render_sidebar, render_welcome


STATUS_LABELS = {
    "understanding": "Understanding your question",
    "finding_data": "Finding relevant Census data",
    "querying": "Querying Census data",
    "preparing_answer": "Preparing the answer",
}

SAFE_FAILURE = "I couldn't complete that request. Please try again."


def _consume_turn(service: ChatService, request) -> None:
    state = st.session_state["chat_state"]
    terminal = False
    with st.status("Working on your question…") as status:
        try:
            for event in service.stream_turn(request):
                if isinstance(event, StatusEvent):
                    status.update(label=STATUS_LABELS[event.stage])
                elif isinstance(event, ResultEvent):
                    finish_turn(state, request.request_id, event.result)
                    terminal = True
                    break
                elif isinstance(event, ErrorEvent):
                    fail_turn(state, request.request_id, event.message)
                    terminal = True
                    break
            if not terminal:
                fail_turn(state, request.request_id, SAFE_FAILURE)
        except Exception:
            fail_turn(state, request.request_id, SAFE_FAILURE)
        finally:
            status.update(label="Response ready" if terminal else "Request ended", state="complete")


def render_app(service: ChatService) -> None:
    """Render one browser session with an injected chat service."""
    st.set_page_config(page_title="Census Compass", page_icon="🧭", layout="centered")
    state = st.session_state.get("chat_state")
    if state is None:
        state = new_state()
        st.session_state["chat_state"] = state

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
    question = example or typed
    if question:
        request = begin_turn(state, question)
        _consume_turn(service, request)
        st.rerun()


if __name__ == "__main__":
    render_app(UnavailableChatService())
