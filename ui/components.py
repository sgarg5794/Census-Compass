"""Native Streamlit components for the Census Compass chat page."""

from typing import TYPE_CHECKING

import pandas as pd
import streamlit as st

if TYPE_CHECKING:
    from census_compass.chat.state import ChatMessage, ChatState


EXAMPLE_QUESTIONS = (
    "What was California’s population in 2020?",
    "Compare the 2020 populations of California and Texas.",
    "How many households rent versus own in San Francisco County?",
    "What was California’s unemployment rate in the 2020-labeled data?",
)


def render_welcome() -> str | None:
    st.subheader("What would you like to explore?")
    st.write("Ask about US population, housing, income and more. Follow up naturally to explore further.")
    columns = st.columns(2)
    for index, question in enumerate(EXAMPLE_QUESTIONS):
        with columns[index % 2]:
            if st.button(question, key=f"example-{index}", use_container_width=True):
                return question
    return None


def render_sidebar(state: "ChatState") -> tuple[str, str | None] | None:
    with st.sidebar:
        if st.button("New chat", disabled=state.pending_request is not None, use_container_width=True):
            return ("new", None)
        st.caption("Conversation history")
        for conversation in reversed(tuple(state.conversations.values())):
            if not conversation.messages:
                continue
            if st.button(
                conversation.title,
                key=f"conversation-{conversation.id}",
                disabled=state.pending_request is not None,
                use_container_width=True,
            ):
                return ("select", conversation.id)
    return None


def render_message(message: "ChatMessage") -> None:
    with st.chat_message(message.role):
        st.markdown(message.text)
        result = message.result
        if result is None:
            return

        if result.truncated:
            st.warning("This result was truncated; the answer may be incomplete.")
        for evidence in result.evidence:
            for limitation in evidence.limitations:
                st.warning(limitation)

        if result.rows:
            st.dataframe(pd.DataFrame.from_records(result.rows), use_container_width=True, hide_index=True)

        if result.evidence:
            with st.expander("View query & source details"):
                for evidence in result.evidence:
                    if evidence.source_label:
                        st.write(f"Source: {evidence.source_label}")
                    if evidence.period_label:
                        st.write(f"Period: {evidence.period_label}")
                    if evidence.executed_sql:
                        st.code(evidence.executed_sql, language="sql")
