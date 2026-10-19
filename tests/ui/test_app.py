"""Observable Census Compass page behavior."""

from decimal import Decimal
from pathlib import Path

from streamlit.testing.v1 import AppTest

from census_compass.chat.contracts import ChatResult, Evidence, ResultEvent
from census_compass.contracts import Outcome
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


def _run(service=None):
    test = AppTest.from_function(
        _app, args=(service or ScriptedChatService(), str(Path(__file__).parents[2]))
    )
    test.run()
    assert not test.exception, test.exception
    return test


def _answer(*, evidence=None, rows=None):
    return ChatResult(
        outcome=Outcome.ANSWERED,
        answer="A sourced answer.",
        rows=rows or [],
        evidence=evidence or [],
        completeness=[],
        truncated=False,
    )


def test_welcome_has_exact_data_examples_and_composer():
    at = _run()
    assert [item.value for item in at.title] == ["Census Compass"]
    labels = [item.label for item in at.button]
    for question in QUESTIONS:
        assert question in labels
    assert len(at.chat_input) == 1
    assert at.chat_input[0].placeholder == "Ask a question about Census data…"


def test_sidebar_has_only_new_chat_and_conversation_history():
    at = _run()
    assert [button.label for button in at.sidebar.button] == ["New chat"]
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
