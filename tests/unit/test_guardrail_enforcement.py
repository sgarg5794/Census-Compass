"""Guardrail enforcement end to end: real ADK runner, scripted LLM, fake classifier.

No network. Spy tools record every tool call; the scripted LLM records every call.
"""

import asyncio
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from census_compass.agent import INSTRUCTION, root_agent
from census_compass.guardrails import gate
from census_compass.guardrails.contracts import Decision
from census_compass.guardrails.replies import MANIPULATION, OFF_TOPIC, reply_for
from census_compass.tools.glossary import load_glossary, validate_glossary

REPO = Path(__file__).resolve().parents[2]
DATA_TOOLS = {
    "resolve_year",
    "resolve_population_group",
    "resolve_geography",
    "search_schema",
    "get_schema",
    "execute_query",
}


def _call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class ScriptedLlm(BaseLlm):
    """Plays a fixed list of tool calls, then a final text; records every request."""

    model: str = "scripted"
    script: list = []
    requests: list = []

    async def generate_content_async(self, llm_request, stream=False):
        self.requests.append(llm_request.config.system_instruction)
        step = sum(
            1 for c in llm_request.contents if c.parts and c.parts[0].function_response is not None
        )
        if step < len(self.script):
            name, args = self.script[step]
            parts = [_call(name, **args)]
        else:
            parts = [types.Part(text="agent ran")]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


def _run(message, monkeypatch, decision=None, script=(), classifier_raises=False):
    llm = ScriptedLlm(script=list(script), requests=[])
    tool_calls = []

    async def fake(msg, context, *, timeout):
        if classifier_raises:
            raise RuntimeError("classifier down")
        return decision

    monkeypatch.setattr(gate, "classify_message", fake)

    def spy(tool, args, tool_context):
        tool_calls.append((tool.name, dict(args)))
        return {"entries": [], "message": "spy"} if tool.name == "lookup_glossary" else {}

    agent = root_agent.model_copy(update={"model": llm, "before_tool_callback": spy})
    runner = InMemoryRunner(agent=agent, app_name="census_compass")

    async def go():
        session = await runner.session_service.create_session(
            app_name="census_compass", user_id="u"
        )
        content = types.Content(role="user", parts=[types.Part(text=message)])
        return [
            e
            async for e in runner.run_async(user_id="u", session_id=session.id, new_message=content)
        ]

    events = asyncio.run(go())
    texts = [
        p.text for e in events if e.content and e.content.parts for p in e.content.parts if p.text
    ]
    return texts, llm, tool_calls


@pytest.mark.parametrize(
    "decision,expected",
    [
        (Decision(label="off_topic"), OFF_TOPIC),
        (Decision(label="inappropriate"), reply_for("inappropriate")),
        (Decision(label="manipulation"), MANIPULATION),
    ],
)
def test_off_topic_inappropriate_manipulation_use_no_tools_and_no_llm(
    monkeypatch, decision, expected
):
    texts, llm, tools = _run(
        "blocked message", monkeypatch, decision, script=[("resolve_year", {"text": "2020"})]
    )
    assert texts == [expected]
    assert llm.requests == [] and tools == []


def test_classifier_failure_reaches_agent(monkeypatch):
    texts, llm, tools = _run("hello", monkeypatch, classifier_raises=True)
    assert texts == ["agent ran"] and len(llm.requests) == 1 and tools == []


def test_classifier_none_reaches_agent(monkeypatch):
    texts, llm, _ = _run("hello", monkeypatch, decision=None)
    assert texts == ["agent ran"] and len(llm.requests) == 1


def test_general_question_route_calls_glossary_not_data_tools(monkeypatch):
    question = "what is a margin of error?"
    texts, llm, tools = _run(
        question,
        monkeypatch,
        Decision(label="general"),
        script=[("lookup_glossary", {"question": question})],
    )
    assert tools == [("lookup_glossary", {"question": question})]
    assert not DATA_TOOLS & {n for n, _ in tools}
    assert texts[-1] == "agent ran"
    assert llm.requests[0].startswith(INSTRUCTION + "\n\n" + gate.routing_hint("general"))


def test_mixed_route_hint_reaches_agent(monkeypatch):
    _, llm, _ = _run(
        "population of Ohio and a poem",
        monkeypatch,
        Decision(label="mixed", census_part="distinctive-census-part-marker"),
    )
    assert len(llm.requests) == 1
    assert gate.routing_hint("mixed") in llm.requests[0]
    assert "distinctive-census-part-marker" not in llm.requests[0]


def test_data_question_unaffected_when_classifier_says_data(monkeypatch):
    texts, llm, tools = _run(
        "unemployment in Cook County",
        monkeypatch,
        Decision(label="data"),
        script=[("resolve_year", {"text": "2020"})],
    )
    assert [n for n, _ in tools] == ["resolve_year"]
    assert llm.requests[0].startswith(INSTRUCTION)
    assert not any(gate.routing_hint(label) in llm.requests[0]
                   for label in ("general", "smalltalk", "mixed"))
    assert texts[-1] == "agent ran"


def test_real_glossary_file_is_valid():
    path = REPO / "definitions/source/census_glossary.yaml"
    assert validate_glossary(load_glossary(path)) == []
