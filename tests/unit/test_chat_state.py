"""Browser-local conversation state and rerun safety."""

import pytest

from census_compass.chat.contracts import ChatResult
from census_compass.chat.state import (
    begin_turn,
    fail_turn,
    finish_turn,
    new_conversation,
    new_state,
    select_conversation,
)
from census_compass.contracts import Outcome


def _answer(text: str = "California had 39 million residents.") -> ChatResult:
    return ChatResult(
        outcome=Outcome.ANSWERED,
        answer=text,
        rows=[],
        evidence=[],
        completeness=[],
        truncated=False,
    )


def test_new_browser_sessions_have_independent_identifiers_and_conversations():
    first = new_state()
    second = new_state()

    assert first.visitor_id != second.visitor_id
    assert first.active_id != second.active_id
    assert first.conversations[first.active_id] is not second.conversations[second.active_id]
    begin_turn(first, "How many people live in California?")
    assert second.conversations[second.active_id].messages == []


def test_switching_conversations_restores_each_transcript():
    state = new_state()
    first_id = state.active_id
    first_request = begin_turn(state, "California population?")
    finish_turn(state, first_request.request_id, _answer("California answer"))

    second_id = new_conversation(state)
    second_request = begin_turn(state, "Texas population?")
    finish_turn(state, second_request.request_id, _answer("Texas answer"))

    select_conversation(state, first_id)
    assert [message.text for message in state.conversations[state.active_id].messages] == [
        "California population?",
        "California answer",
    ]
    select_conversation(state, second_id)
    assert [message.text for message in state.conversations[state.active_id].messages] == [
        "Texas population?",
        "Texas answer",
    ]


def test_first_question_sets_trimmed_single_line_title_limited_to_sixty_characters():
    state = new_state()
    question = "  What was the population\nof California in 2020, and how does it compare?  "

    begin_turn(state, question)

    assert state.conversations[state.active_id].title == (
        "What was the population of California in 2020, and how does"
    )
    assert len(state.conversations[state.active_id].title) <= 60


def test_later_questions_do_not_replace_first_title():
    state = new_state()
    first = begin_turn(state, "California population?")
    finish_turn(state, first.request_id, _answer())

    begin_turn(state, "Texas population?")

    assert state.conversations[state.active_id].title == "California population?"


def test_repeated_completion_adds_only_one_assistant_message_and_cannot_reopen_turn():
    state = new_state()
    request = begin_turn(state, "California population?")
    result = _answer()

    finish_turn(state, request.request_id, result)
    finish_turn(state, request.request_id, result)

    conversation = state.conversations[state.active_id]
    assert [message.role for message in conversation.messages] == ["user", "assistant"]
    assert conversation.messages[1].result == result
    assert conversation.processed_request_ids == {request.request_id}
    assert state.pending_request is None


def test_concurrent_submission_and_history_navigation_are_rejected():
    state = new_state()
    other_id = new_conversation(state)
    original_id = next(conversation_id for conversation_id in state.conversations if conversation_id != other_id)
    select_conversation(state, original_id)
    request = begin_turn(state, "California population?")

    with pytest.raises(ValueError):
        begin_turn(state, "Texas population?")
    with pytest.raises(ValueError):
        select_conversation(state, other_id)
    with pytest.raises(ValueError):
        new_conversation(state)

    assert state.active_id == original_id
    assert state.pending_request == request
    assert len(state.conversations[original_id].messages) == 1


def test_unknown_conversation_id_is_rejected_without_changing_selection():
    state = new_state()
    original_id = state.active_id

    with pytest.raises(ValueError):
        select_conversation(state, "missing")

    assert state.active_id == original_id


def test_blank_draft_does_not_enter_history_or_make_request_pending():
    state = new_state()

    with pytest.raises(ValueError):
        begin_turn(state, " \n\t ")

    assert state.conversations[state.active_id].messages == []
    assert state.pending_request is None


def test_failed_turn_adds_safe_assistant_message_and_releases_pending_state():
    state = new_state()
    request = begin_turn(state, "California population?")

    fail_turn(state, request.request_id, "The service is unavailable.")
    fail_turn(state, request.request_id, "The service is unavailable.")

    conversation = state.conversations[state.active_id]
    assert [message.text for message in conversation.messages] == [
        "California population?",
        "The service is unavailable.",
    ]
    assert conversation.messages[-1].result is None
    assert conversation.processed_request_ids == {request.request_id}
    assert state.pending_request is None
    assert begin_turn(state, "Texas population?").text == "Texas population?"


def test_unknown_completion_is_rejected_without_mutating_transcript():
    state = new_state()
    request = begin_turn(state, "California population?")

    with pytest.raises(ValueError):
        finish_turn(state, "unknown", _answer())

    assert state.pending_request == request
    assert [message.role for message in state.conversations[state.active_id].messages] == ["user"]
