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
            if st.button(question, key=f"example-{index}", width="stretch", wrap=True):
                return question
    return None


def render_sidebar(state: "ChatState") -> tuple[str, str | None] | None:
    action = None
    with st.sidebar:
        if st.button("New chat", disabled=state.pending_request is not None, width="stretch"):
            action = ("new", None)
        st.caption("Conversation history")
        for conversation in reversed(tuple(state.conversations.values())):
            if not conversation.messages:
                continue
            if st.button(
                conversation.title,
                key=f"conversation-{conversation.id}",
                disabled=state.pending_request is not None,
                width="stretch",
            ):
                action = ("select", conversation.id)
    return action


def render_message(message: "ChatMessage") -> None:
    with st.chat_message(message.role):
        st.markdown(message.text)
        result = message.result
        if result is None:
            return

        if result.truncated:
            st.warning("This result was truncated; the answer may be incomplete.")
        periods = list(dict.fromkeys(e.period_label for e in result.evidence if e.period_label))
        for period in periods:
            st.caption(f"Reporting period: {period}")
        for item in result.completeness:
            parts = [f"Row {item.row + 1}, {item.column}"]
            if item.coverage is not None:
                parts.append(f"{item.coverage:.0%} coverage")
            parts.append(f"{item.block_groups} block groups")
            if item.missing_inputs:
                missing = ", ".join(
                    f"{name}: {count}" for name, count in item.missing_inputs.items()
                )
                parts.append(f"missing inputs {missing}")
            if item.unknown_denominator is not None:
                parts.append(f"unknown denominator: {item.unknown_denominator}")
            if item.missing_moe is not None:
                parts.append(f"missing margin of error: {item.missing_moe}")
            st.caption(" · ".join(parts))
        for evidence in result.evidence:
            for limitation in evidence.limitations:
                st.warning(limitation)

        if result.rows:
            st.dataframe(pd.DataFrame.from_records(result.rows), width="stretch", hide_index=True)

        if result.evidence:
            with st.expander("View query & source details"):
                for evidence in result.evidence:
                    if evidence.source_label:
                        st.write(f"Source: {evidence.source_label}")
                    if evidence.period_label:
                        st.write(f"Period: {evidence.period_label}")
                    if evidence.executed_sql:
                        st.code(evidence.executed_sql, language="sql")
