"""Observable Census Compass page behavior."""

from decimal import Decimal
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from census_compass.chat.contracts import ChatResult, ErrorEvent, Evidence, ResultEvent
from census_compass.chat.service import UnavailableChatService
from census_compass.chat.state import begin_turn
from census_compass.contracts import Completeness, Outcome
from tests.fixtures.chat import ScriptedChatService

QUESTIONS = [
    "What was California’s population in 2020?",
    "Compare the 2020 populations of California and Texas.",
    "How many households rent versus own in San Francisco County?",
    "What was California’s unemployment rate in the 2020-labeled data?",
]


def _app(service, root):
    import sys

    sys.path.insert(0, root)
    from ui.app import render_app

    render_app(service)


def _bootstrap_app(root):
    import sys

    sys.path.insert(0, root)
    from ui.app import _session_service, render_app

    render_app(_session_service())


def _run(service=None):
    test = AppTest.from_function(
        _app, args=(service or ScriptedChatService(), str(Path(__file__).parents[2]))
    )
    test.run()
    assert not test.exception, test.exception
    return test


def _answer(*, evidence=None, rows=None, outcome=Outcome.ANSWERED, answer="A sourced answer."):
    return ChatResult(
        outcome=outcome,
        answer=answer,
        rows=rows or [],
        evidence=evidence or [],
        completeness=[],
        truncated=False,
    )


def test_welcome_has_exact_data_examples_and_composer():
    at = _run()
    assert [item.value for item in at.title] == ["Census Compass"]
    labels = [item.label for item in at.button]
    assert sorted(labels) == sorted(["New chat", *QUESTIONS])
    assert all(button.proto.wrap for button in at.button if button.label in QUESTIONS)
    assert len(at.chat_input) == 1
    assert at.chat_input[0].placeholder == "Ask a question about Census data…"


def test_sidebar_has_only_new_chat_and_conversation_history():
    at = _run()
    assert [button.label for button in at.sidebar.button] == ["New chat"]
    assert len(at.sidebar.caption) == 1
    sidebar_text = " ".join(item.value for item in at.sidebar.caption)
    assert "Conversation history" in sidebar_text
    for forbidden in ("Schema", "Dataset", "Explorer", "Coverage", "Settings"):
        assert forbidden not in sidebar_text


def test_example_submits_exact_text_once():
    service = ScriptedChatService([ResultEvent(result=_answer())])
    at = _run(service)
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    assert [request.text for request in service.requests] == [QUESTIONS[0]]
    assert any(message.markdown[0].value == QUESTIONS[0] for message in at.chat_message)
    at.run()
    assert len(service.requests) == 1


def test_answer_without_evidence_has_no_query_details():
    service = ScriptedChatService([ResultEvent(result=_answer())])
    at = _run(service)
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    assert "View query & source details" not in [item.label for item in at.expander]


def test_structured_rows_preserve_missing_values_and_show_executed_sql():
    result = _answer(
        rows=[{"State": "California", "Value": Decimal("39.125"), "Missing": None}],
        evidence=[
            Evidence(
                executed_sql="SELECT value FROM census_table",
                source_label="Census source",
                period_label="2020-labeled data",
                limitations=[],
            )
        ],
    )
    service = ScriptedChatService([ResultEvent(result=result)])
    at = _run(service)
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    assert len(at.dataframe) == 1
    frame = at.dataframe[0].value
    assert str(frame.loc[0, "Value"]) == "39.125"
    assert frame.loc[0, "Missing"] is None
    assert [item.label for item in at.expander] == ["View query & source details"]
    assert any(code.language == "sql" and "SELECT value" in code.value for code in at.code)


def test_source_period_and_partial_coverage_are_visible_with_answer():
    result = _answer(
        evidence=[
            Evidence(
                executed_sql="SELECT value FROM census_table",
                source_label="Census source",
                period_label="2020-labeled data",
                limitations=[],
            )
        ]
    )
    result.completeness = [
        Completeness(
            row=0,
            column="Population",
            block_groups=3,
            missing_inputs={"Estimate": 1},
            coverage=0.75,
        )
    ]
    at = _run(ScriptedChatService([ResultEvent(result=result)]))
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    visible_notes = " ".join(item.value for item in at.chat_message[-1].caption)
    assert "2020-labeled data" in visible_notes
    assert "75%" in visible_notes
    assert "Population" in visible_notes
    assert "Estimate" in visible_notes


def test_truncation_and_limitations_remain_visible_with_long_table_text():
    long_label = "Long geography label " * 100
    result = _answer(
        rows=[{"Geography": long_label, "Value": None}],
        evidence=[
            Evidence(
                executed_sql=None,
                source_label="Census source",
                period_label=None,
                limitations=["Only published block groups were included."],
            )
        ],
    )
    result.truncated = True
    at = _run(ScriptedChatService([ResultEvent(result=result)]))
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    warnings = " ".join(item.value for item in at.chat_message[-1].warning)
    assert "truncated" in warnings.lower()
    assert "Only published block groups were included." in warnings
    assert at.dataframe[0].value.loc[0, "Geography"] == long_label


@pytest.mark.parametrize(
    ("outcome", "answer"),
    [
        (Outcome.CLARIFICATION_NEEDED, "Which Census year do you mean?"),
        (Outcome.UNSUPPORTED, "That request is outside the Census dataset."),
        (Outcome.NOT_AVAILABLE, "That geography is unavailable."),
        (Outcome.TIMED_OUT, "The request timed out."),
        (Outcome.DATABASE_FAILURE, "The database could not answer."),
        (Outcome.MODEL_FAILURE, "The assistant could not answer."),
    ],
)
def test_terminal_result_outcomes_are_visible_and_release_chat(outcome, answer):
    service = ScriptedChatService([ResultEvent(result=_answer(outcome=outcome, answer=answer))])
    at = _run(service)
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    assert at.chat_message[-1].markdown[0].value == answer
    assert at.chat_input[0].disabled is False
    assert len(service.requests) == 1


def test_typed_submission_uses_same_path_and_history_selection_does_not_replay():
    service = ScriptedChatService([ResultEvent(result=_answer())])
    at = _run(service)
    at.chat_input[0].set_value("What is the population?").run()
    assert not at.exception, at.exception
    assert [request.text for request in service.requests] == ["What is the population?"]
    assert not [button for button in at.button if button.label in QUESTIONS]
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception
    assert len(service.requests) == 1
    assert len(at.sidebar.button) == 2
    at.sidebar.button[1].click().run()
    assert not at.exception, at.exception
    assert at.chat_message[0].markdown[0].value == "What is the population?"
    assert len(service.requests) == 1


def test_off_topic_question_displays_census_redirect_and_keeps_history_usable():
    question = "What is the weather in New York today?"
    redirect = "I can help with Census population, housing, and employment data. Try asking about a state or county."
    service = ScriptedChatService(
        [ResultEvent(result=_answer(outcome=Outcome.UNSUPPORTED, answer=redirect))]
    )
    at = _run(service)
    at.chat_input[0].set_value(question).run()
    assert not at.exception, at.exception
    assert [request.text for request in service.requests] == [question]
    assert at.chat_message[-1].markdown[0].value == redirect
    assert at.chat_input[0].disabled is False
    at.sidebar.button[0].click().run()
    assert not at.exception, at.exception
    at.sidebar.button[1].click().run()
    assert not at.exception, at.exception
    assert at.chat_message[-1].markdown[0].value == redirect
    assert len(service.requests) == 1


@pytest.mark.parametrize(
    "events,failure",
    [
        ([ErrorEvent(message="internal password=secret")], None),
        ([], TimeoutError("secret transport detail")),
        ([], RuntimeError("database credentials")),
        ([], None),
        ([ResultEvent(result=_answer()), ErrorEvent(message="duplicate")], None),
    ],
)
def test_error_paths_append_one_safe_failure_and_reenable_chat(events, failure):
    service = ScriptedChatService(events, failure=failure)
    at = _run(service)
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    assert len(at.chat_message) == 2
    message = at.chat_message[-1].markdown[0].value
    assert "couldn't complete" in message
    assert "secret" not in message
    assert "credentials" not in message
    assert at.chat_input[0].disabled is False
    at.run()
    assert len(service.requests) == 1


def test_missing_backend_is_honest_and_never_returns_a_scripted_answer():
    at = _run(UnavailableChatService())
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    text = at.chat_message[-1].markdown[0].value
    assert "temporarily unavailable" in text
    assert "A sourced answer" not in text


def test_interrupted_pending_turn_becomes_failure_without_replay():
    service = ScriptedChatService([ResultEvent(result=_answer())])
    at = _run(service)
    begin_turn(at.session_state["chat_state"], "Interrupted question")
    at.run()
    assert not at.exception, at.exception
    assert len(service.requests) == 0
    assert at.chat_message[-1].markdown[0].value.startswith("I couldn't complete")
    assert at.chat_input[0].disabled is False


def test_whitespace_only_input_is_ignored_and_composer_remains_usable():
    service = ScriptedChatService([ResultEvent(result=_answer())])
    at = _run(service)
    at.chat_input[0].set_value("   \n  ").run()
    assert not at.exception, at.exception
    assert not service.requests
    assert at.chat_input[0].disabled is False
    assert not at.chat_message


def test_bootstrap_keeps_one_service_for_browser_session(monkeypatch):
    import ui.app

    created = []

    def create():
        service = ScriptedChatService([ResultEvent(result=_answer())])
        created.append(service)
        return service

    monkeypatch.setattr(ui.app, "create_chat_service", create)
    at = AppTest.from_function(_bootstrap_app, args=(str(Path(__file__).parents[2]),))
    at.run()
    assert not at.exception, at.exception
    at.button(key="example-0").click().run()
    assert not at.exception, at.exception
    at.run()
    assert len(created) == 1
    assert len(created[0].requests) == 1
    assert at.session_state["chat_service"] is created[0]


def test_browser_sessions_keep_separate_visitors_and_transcripts():
    first = _run(ScriptedChatService([ResultEvent(result=_answer())]))
    second = _run(ScriptedChatService([ResultEvent(result=_answer())]))
    first.button(key="example-0").click().run()
    assert not first.exception, first.exception
    assert first.session_state["chat_state"].visitor_id != second.session_state["chat_state"].visitor_id
    assert len(first.chat_message) == 2
    assert not second.chat_message
