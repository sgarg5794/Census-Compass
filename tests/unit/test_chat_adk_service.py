"""The chat adapter's visitor, session, and display boundaries."""

from collections.abc import Iterator

from census_compass.chat.adk_service import AdkChatService, create_chat_service
from census_compass.chat.contracts import (
    ChatEvent,
    ChatRequest,
    ErrorEvent,
    ResultEvent,
    StatusEvent,
)
from census_compass.chat.service import UnavailableChatService


class RecordingRuntime:
    def __init__(self, events: list[object] | None = None):
        self.events = events if events is not None else []
        self.requests: list[ChatRequest] = []
        self.closed = False

    def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
        self.requests.append(request)
        yield from self.events

    def close(self) -> None:
        self.closed = True


def request(visitor: str, conversation: str, text: str = "Question?") -> ChatRequest:
    return ChatRequest(visitor_id=visitor, conversation_id=conversation, request_id="turn", text=text)


def result_event(sql: str | None = None) -> dict:
    return {"kind": "result", "result": {"outcome": "answered", "answer": "42 people.",
            "rows": [], "evidence": [{"executed_sql": sql, "source_label": "Census",
                                       "period_label": "2020", "limitations": []}],
            "completeness": [], "truncated": False}}


def test_same_conversation_keeps_runtime_context_for_clarification_reply():
    runtime = RecordingRuntime([result_event()])
    service = AdkChatService(runtime)
    list(service.stream_turn(request("visitor", "conversation", "Which year?")))
    list(service.stream_turn(request("visitor", "conversation", "2020")))
    assert len(runtime.requests) == 2
    assert runtime.requests[0].conversation_id == runtime.requests[1].conversation_id
    assert runtime.requests[0].conversation_id != "conversation"
    assert [item.text for item in runtime.requests] == ["Which year?", "2020"]


def test_new_chat_gets_separate_runtime_session():
    runtime = RecordingRuntime([result_event()])
    service = AdkChatService(runtime)
    list(service.stream_turn(request("visitor", "one")))
    list(service.stream_turn(request("visitor", "two")))
    assert runtime.requests[0].conversation_id != runtime.requests[1].conversation_id


def test_foreign_visitor_cannot_reuse_conversation():
    runtime = RecordingRuntime([result_event()])
    service = AdkChatService(runtime)
    list(service.stream_turn(request("owner", "shared")))
    events = list(service.stream_turn(request("intruder", "shared")))
    assert len(events) == 1 and isinstance(events[0], ErrorEvent)
    assert len(runtime.requests) == 1


def test_visitor_ids_are_isolated_at_runtime_boundary():
    runtime = RecordingRuntime([result_event()])
    service = AdkChatService(runtime)
    list(service.stream_turn(request("first", "one")))
    list(service.stream_turn(request("second", "two")))
    assert runtime.requests[0].visitor_id != runtime.requests[1].visitor_id
    assert runtime.requests[0].visitor_id != "first"


def test_raw_events_and_secret_errors_are_not_displayed():
    runtime = RecordingRuntime([{"kind": "status", "stage": "understanding"},
                                {"kind": "tool", "prompt": "secret", "reasoning": "hidden"},
                                {"kind": "error", "message": "secret api token"}])
    events = list(AdkChatService(runtime).stream_turn(request("visitor", "chat")))
    assert events[0] == StatusEvent(stage="understanding")
    assert len(events) == 2 and isinstance(events[1], ErrorEvent)
    assert "secret" not in repr(events)


def test_sql_is_displayed_only_from_structured_executed_evidence():
    runtime = RecordingRuntime([{"kind": "status", "stage": "querying", "sql": "unverified"},
                                result_event("SELECT 42")])
    events = list(AdkChatService(runtime).stream_turn(request("visitor", "chat")))
    assert len(events) == 1 and isinstance(events[0], ResultEvent)
    assert events[0].result.evidence[0].executed_sql == "SELECT 42"
    assert "unverified" not in repr(events)


def test_runtime_failure_is_generic_and_does_not_retry():
    class FailingRuntime(RecordingRuntime):
        def stream_turn(self, request: ChatRequest) -> Iterator[ChatEvent]:
            self.requests.append(request)
            raise RuntimeError("private database credential")
            yield  # pragma: no cover

    runtime = FailingRuntime()
    events = list(AdkChatService(runtime).stream_turn(request("visitor", "chat")))
    assert len(events) == 1 and isinstance(events[0], ErrorEvent)
    assert "private" not in events[0].message
    assert len(runtime.requests) == 1


def test_runtime_initialization_failure_is_honestly_unavailable(monkeypatch):
    from census_compass.chat import adk_service

    def unavailable():
        raise RuntimeError("private configuration detail")

    monkeypatch.setattr(adk_service, "create_agent_runtime", unavailable)
    service = create_chat_service()
    assert isinstance(service, UnavailableChatService)
    events = list(service.stream_turn(request("visitor", "chat")))
    assert len(events) == 1 and isinstance(events[0], ErrorEvent)


def test_default_factory_uses_the_application_runtime_when_available(monkeypatch):
    from census_compass.chat import adk_service

    runtime = RecordingRuntime([result_event()])
    monkeypatch.setattr(adk_service, "create_agent_runtime", lambda: runtime)
    service = adk_service.create_chat_service()
    events = list(service.stream_turn(request("visitor", "chat")))
    assert isinstance(events[-1], ResultEvent)
    assert len(runtime.requests) == 1


def test_close_releases_runtime_and_stops_future_turns():
    runtime = RecordingRuntime([result_event()])
    service = AdkChatService(runtime)
    service.close()
    service.close()
    events = list(service.stream_turn(request("visitor", "chat")))
    assert runtime.closed
    assert len(events) == 1 and isinstance(events[0], ErrorEvent)
    assert not runtime.requests
