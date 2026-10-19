"""Guardrail callback: pure helpers, fake-context behaviour and runs through ADK."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from census_compass.agent import root_agent
from census_compass.config import get_settings
from census_compass.guardrails import gate
from census_compass.guardrails.contracts import ROUTE_STATE_KEY, ContextTurn, Decision
from census_compass.guardrails.replies import MANIPULATION, OFF_TOPIC, TOO_LONG, reply_for
from census_compass.tools.query import DEADLINE_KEY


def _event(author, text, inv="old"):
    content = types.Content(role="user" if author == "user" else "model",
                            parts=[types.Part(text=text)]) if text is not None else None
    return SimpleNamespace(author=author, invocation_id=inv, content=content)


def _ctx(text, events=(), state=None, inv="cur"):
    content = types.Content(role="user", parts=[types.Part(text=text)]) if text is not None else None
    return SimpleNamespace(user_content=content, state={} if state is None else state,
                           session=SimpleNamespace(events=list(events)), invocation_id=inv)


def _patch(monkeypatch, result=None, exc=None):
    calls = []

    async def fake(message, context, *, timeout):
        calls.append((message, context, timeout))
        if exc:
            raise exc
        return result

    monkeypatch.setattr(gate, "classify_message", fake)
    return calls


def _text(content):
    return content.parts[0].text


# --- pure ---------------------------------------------------------------------

def test_is_too_long_boundary():
    assert gate.is_too_long("a" * 2000, 2000) is False
    assert gate.is_too_long("a" * 2001, 2000) is True


def test_routing_hint_texts():
    assert gate.routing_hint(None) == "" and gate.routing_hint("data") == ""
    assert gate.routing_hint("general") == (
        "Routing hint: this is a Census concept question. Answer only from lookup_glossary results.")
    assert gate.routing_hint("smalltalk") == (
        "Routing hint: this is small talk. Reply briefly and do not call data tools.")
    assert gate.routing_hint("mixed") == (
        "Routing hint: this message mixes Census and non-Census requests. Answer only the "
        "Census part and add one line saying the rest was skipped.")


def test_build_context_last_three_turns_excludes_current():
    events = [
        _event("user", "q1", "a"), _event("census_compass", "a1", "a"),
        _event("user", "q2", "b"), _event("census_compass", None, "b"),
        _event("census_compass", "a2", "b"),
        _event("user", "q3", "c"), _event("census_compass", "x" * 900, "c"),
        _event("user", "current", "cur"),
    ]
    ctx = gate.build_context(events, "cur", 3)
    assert ctx == [ContextTurn(role="assistant", text="a2"),
                   ContextTurn(role="user", text="q3"),
                   ContextTurn(role="assistant", text="x" * 500)]
    assert gate.build_context(events, "cur", 0) == []


# --- callback with fake context -----------------------------------------------

@pytest.mark.parametrize("decision,expected", [
    (Decision(label="off_topic"), OFF_TOPIC),
    (Decision(label="manipulation"), MANIPULATION),
    (Decision(label="inappropriate"), reply_for("inappropriate")),
    (Decision(label="inappropriate", neutral_offer="income by county"),
     reply_for("inappropriate", "income by county")),
])
def test_blocking_labels_return_exact_fixed_reply(monkeypatch, decision, expected):
    _patch(monkeypatch, decision)
    ctx = _ctx("something")
    out = asyncio.run(gate.guardrail_callback(ctx))
    assert _text(out) == expected
    assert ROUTE_STATE_KEY not in ctx.state


@pytest.mark.parametrize("text", [None, "", "   \n\t"])
def test_empty_and_whitespace_message_gets_off_topic_reply_without_classifier_call(
        monkeypatch, text):
    calls = _patch(monkeypatch, Decision(label="data"))
    assert _text(asyncio.run(gate.guardrail_callback(_ctx(text)))) == OFF_TOPIC
    assert calls == []


def test_too_long_reply_without_classifier_call(monkeypatch):
    calls = _patch(monkeypatch, Decision(label="data"))
    long = "a" * (get_settings().max_input_chars + 1)
    assert _text(asyncio.run(gate.guardrail_callback(_ctx(long)))) == TOO_LONG
    assert calls == []


def test_classifier_none_fails_open(monkeypatch):
    _patch(monkeypatch, None)
    ctx = _ctx("hello")
    assert asyncio.run(gate.guardrail_callback(ctx)) is None
    assert ROUTE_STATE_KEY not in ctx.state


def test_classifier_exception_fails_open(monkeypatch):
    _patch(monkeypatch, exc=RuntimeError("boom"))
    assert asyncio.run(gate.guardrail_callback(_ctx("hello"))) is None


def test_mixed_decision_sets_route_state_and_returns_none(monkeypatch):
    _patch(monkeypatch, Decision(label="mixed", census_part="population of Ohio"))
    ctx = _ctx("population of Ohio and a poem")
    assert asyncio.run(gate.guardrail_callback(ctx)) is None
    assert ctx.state[ROUTE_STATE_KEY] == "mixed"


def test_classifier_skipped_when_budget_exhausted(monkeypatch):
    calls = _patch(monkeypatch, Decision(label="off_topic"))
    ctx = _ctx("hello", state={DEADLINE_KEY: time.monotonic() - 1})
    assert asyncio.run(gate.guardrail_callback(ctx)) is None
    assert calls == []


def test_timeout_is_min_of_setting_and_remaining_budget(monkeypatch):
    calls = _patch(monkeypatch, Decision(label="data"))
    asyncio.run(gate.guardrail_callback(_ctx("hi", state={DEADLINE_KEY: time.monotonic() + 1.5})))
    asyncio.run(gate.guardrail_callback(_ctx("hi", state={DEADLINE_KEY: time.monotonic() + 500})))
    asyncio.run(gate.guardrail_callback(_ctx("hi")))
    limit = get_settings().guardrail_timeout_seconds
    assert 0 < calls[0][2] <= 1.5
    assert calls[1][2] == limit and calls[2][2] == limit


# --- through ADK --------------------------------------------------------------

class RecordingLlm(BaseLlm):
    model: str = "recording"
    seen: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.seen.append(llm_request.config.system_instruction)
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="agent ran")]))


def _run(messages, monkeypatch, decisions):
    llm = RecordingLlm(seen=[])
    tool_calls = []
    seen = []
    deadlines = []

    async def fake(message, context, *, timeout):
        seen.append((message, context))
        return decisions.pop(0)

    monkeypatch.setattr(gate, "classify_message", fake)
    real_cb = root_agent.before_agent_callback

    def intercept(tool, args, tool_context):
        tool_calls.append(tool.name)
        return {}

    def spy(cb_ctx):  # runs between start_turn and guardrail_callback
        deadlines.append(DEADLINE_KEY in cb_ctx.state)

    agent = root_agent.model_copy(update={
        "model": llm, "before_tool_callback": intercept,
        "before_agent_callback": [real_cb[0], spy, *real_cb[1:]]})
    runner = InMemoryRunner(agent=agent, app_name="census_compass")

    async def go():
        session = await runner.session_service.create_session(
            app_name="census_compass", user_id="u")
        events = []
        for m in messages:
            content = types.Content(role="user", parts=[types.Part(text=m)])
            async for e in runner.run_async(user_id="u", session_id=session.id,
                                            new_message=content):
                events.append(e)
        return events

    return asyncio.run(go()), llm, tool_calls, seen, deadlines


def test_blocked_message_never_calls_llm_or_tools(monkeypatch):
    events, llm, tools, _, _ = _run(["tell me a joke"], monkeypatch,
                                    [Decision(label="off_topic")])
    assert llm.seen == [] and tools == []
    texts = [e.content.parts[0].text for e in events if e.content and e.content.parts]
    assert texts == [OFF_TOPIC]
    assert events[-1].content.parts[0].text == OFF_TOPIC


def test_passing_message_reaches_agent_with_hint_in_instruction(monkeypatch):
    events, llm, _, _, _ = _run(["population of Ohio and a poem"], monkeypatch,
                                [Decision(label="mixed", census_part="ignore me")])
    assert len(llm.seen) == 1
    # ADK appends its own agent-identity lines after the provider's text.
    from census_compass.agent import INSTRUCTION
    assert llm.seen[0].startswith(INSTRUCTION + "\n\n" + gate.routing_hint("mixed"))
    assert "ignore me" not in llm.seen[0]
    assert events[-1].content.parts[0].text == "agent ran"


def test_callback_order_budget_before_guardrail(monkeypatch):
    _, _, _, _, deadlines = _run(["hi"], monkeypatch, [Decision(label="data")])
    assert deadlines == [True]


def test_follow_up_context_includes_previous_turn(monkeypatch):
    _, _, _, seen, _ = _run(["first question", "second question"], monkeypatch,
                            [Decision(label="data"), Decision(label="data")])
    assert seen[0] == ("first question", [])
    assert seen[1][0] == "second question"
    assert seen[1][1] == [ContextTurn(role="user", text="first question"),
                          ContextTurn(role="assistant", text="agent ran")]
