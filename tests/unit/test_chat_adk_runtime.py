from types import SimpleNamespace

from google.genai import types

from census_compass.chat.adk_runtime import CensusAgentRuntime
from census_compass.chat.contracts import ChatRequest, ResultEvent
from census_compass.contracts import Outcome


class FakeRunner:
    def __init__(self):
        self.session_service = self
        self.created = []
        self.runs = []
        self.closed = False

    async def create_session(self, *, app_name, user_id):
        session = SimpleNamespace(id=f"session-{len(self.created)}")
        self.created.append((app_name, user_id, session.id))
        return session

    async def run_async(self, *, user_id, session_id, new_message):
        self.runs.append((user_id, session_id, new_message))
        yield SimpleNamespace(
            get_function_calls=lambda: [],
            is_final_response=lambda: True,
            content=types.Content(role="model", parts=[types.Part(text="There were 39 million people.")]),
        )

    async def close(self):
        self.closed = True


def request(visitor="v1", conversation="c1", text="How many people live in California?"):
    return ChatRequest(visitor_id=visitor, conversation_id=conversation, request_id="r1", text=text)


def test_dataset_turn_uses_reusable_isolated_adk_sessions():
    runner = FakeRunner()
    runtime = CensusAgentRuntime(runner=runner)
    try:
        first = list(runtime.stream_turn(request()))
        second = list(runtime.stream_turn(request(text="What about Texas?")))
        list(runtime.stream_turn(request(conversation="c2")))
        assert isinstance(first[-1], ResultEvent)
        assert first[-1].result.answer == "There were 39 million people."
        assert second[-1].result.outcome == Outcome.ANSWERED
        assert runner.runs[0][1] == runner.runs[1][1]
        assert runner.runs[2][1] != runner.runs[0][1]
        assert len(runner.created) == 2
    finally:
        runtime.close()
    assert runner.closed


def test_off_topic_request_never_reaches_adk():
    runner = FakeRunner()
    runtime = CensusAgentRuntime(runner=runner)
    try:
        events = list(runtime.stream_turn(request(text="What is the weather in San Francisco?")))
        assert len(events) == 1
        assert isinstance(events[0], ResultEvent)
        assert events[0].result.outcome == Outcome.UNSUPPORTED
        assert runner.runs == []
        assert runner.created == []
    finally:
        runtime.close()


def test_general_census_explanation_does_not_use_model_memory():
    runner = FakeRunner()
    runtime = CensusAgentRuntime(runner=runner)
    try:
        events = list(runtime.stream_turn(
            request(text="How does the Census Bureau conduct the ACS?")
        ))
        assert events[0].result.outcome == Outcome.UNSUPPORTED
        assert runner.runs == []
    finally:
        runtime.close()
