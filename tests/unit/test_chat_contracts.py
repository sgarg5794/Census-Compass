import pytest
from pydantic import ValidationError

from census_compass.chat.contracts import (
    ChatRequest,
    ErrorEvent,
    Evidence,
    ResultEvent,
    StatusEvent,
    StatusStage,
    parse_chat_event,
)
from census_compass.chat.service import UnavailableChatService
from census_compass.contracts import Completeness, Outcome


@pytest.mark.parametrize("text", ["", " \t\n "])
def test_chat_request_rejects_blank_text(text):
    with pytest.raises(ValidationError):
        ChatRequest(visitor_id="visitor", conversation_id="conversation", request_id="request", text=text)


def test_status_event_rejects_unknown_stage():
    with pytest.raises(ValidationError):
        StatusEvent.model_validate({"kind": "status", "stage": "thinking"})
    with pytest.raises(ValidationError):
        parse_chat_event({"kind": "status", "stage": "thinking"})


def test_final_result_preserves_null_evidence_and_completeness():
    completeness = Completeness(row=0, column="rent_rate", block_groups=7,
                                missing_inputs={"B25003_003": 2}, coverage=0.71)
    event = ResultEvent.model_validate({
        "kind": "result",
        "result": {
            "outcome": Outcome.ANSWERED,
            "answer": "Renters account for 42%.",
            "rows": [{"county": "Example", "rate": 0.42}],
            "evidence": [{"executed_sql": None, "source_label": None,
                          "period_label": None, "limitations": ["Partial coverage"]}],
            "completeness": [completeness.model_dump()],
            "truncated": False,
        },
    })
    assert event.result.evidence == [Evidence(executed_sql=None, source_label=None,
                                               period_label=None, limitations=["Partial coverage"])]
    assert event.result.completeness == [completeness]
    assert event.result.evidence[0].executed_sql is None
    assert event.result.evidence[0].period_label is None


def test_unavailable_service_emits_one_safe_error_and_no_result():
    service = UnavailableChatService()
    events = list(service.stream_turn(ChatRequest(visitor_id="v", conversation_id="c",
                                                   request_id="r", text="Question?")))
    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert events[0].kind == "error"
    assert events[0].message
    assert all(not isinstance(event, ResultEvent) for event in events)
    assert not any(isinstance(event, StatusEvent) for event in events)
    assert service.close() is None
