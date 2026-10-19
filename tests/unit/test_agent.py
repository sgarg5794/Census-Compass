"""ADK agent wiring: tools, instruction, callbacks and tool-call ordering.

No network: the model is a scripted fake (a BaseLlm subclass) and every tool call
is intercepted by a before_tool_callback that returns a canned result.
"""

import asyncio
import json
import time
from collections.abc import AsyncGenerator
from types import SimpleNamespace

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

from census_compass.agent import (
    MODEL_FAILURE_MESSAGE,
    on_model_error,
    root_agent,
    start_turn,
)
from census_compass.config import get_settings
from census_compass.tools.query import DEADLINE_KEY, REJECTIONS_KEY

TOOLS = ["resolve_year", "resolve_population_group", "resolve_geography",
         "search_schema", "get_schema", "execute_query"]


def test_root_agent_has_exactly_the_six_tools():
    assert sorted(t.__name__ for t in root_agent.tools) == sorted(TOOLS)
    assert root_agent.name == "census_compass"


def test_model_is_litellm_with_configured_model():
    assert isinstance(root_agent.model, LiteLlm)
    assert root_agent.model.model == get_settings().agent_model


def test_instruction_provider_returns_rules_with_literal_braces():
    assert callable(root_agent.instruction)
    text = root_agent.instruction(SimpleNamespace(state={}))
    for phrase in ["Census Compass", "resolve_geography", "model_failure",
                   "never aggregated", "not adjusted for inflation", "{table}"]:
        assert phrase in text
    assert text.index("# Role") < text.index("# Domain rules")


def test_start_turn_sets_budget_and_resets_rejections():
    ctx = SimpleNamespace(state={REJECTIONS_KEY: 2})
    before = time.monotonic()
    assert start_turn(ctx) is None
    budget = get_settings().request_budget_seconds
    assert before + budget <= ctx.state[DEADLINE_KEY] <= time.monotonic() + budget
    assert ctx.state[REJECTIONS_KEY] == 0
    assert DEADLINE_KEY.startswith("temp:") and REJECTIONS_KEY.startswith("temp:")


def test_model_error_returns_model_failure_without_leaking():
    secret = "sk-ant-api03-SECRET"
    error = RuntimeError(f"AuthenticationError: invalid x-api-key {secret}")
    response = on_model_error(callback_context=None, llm_request=None, error=error)
    text = response.content.parts[0].text
    assert text == MODEL_FAILURE_MESSAGE
    assert "language model service" in text and "try again" in text.lower()
    assert secret not in text and "AuthenticationError" not in text


# --- scripted end-to-end runs through InMemoryRunner -------------------------

CANNED = {
    "resolve_year": lambda a: (
        {"status": "ambiguous", "periods": [], "comparison": False,
         "message": "Please say 2019 (2015–2019) or 2020 (2016–2020)."}
        if a["text"] == "2017" else
        {"status": "ok", "periods": [{"year": 2020, "label": "2016–2020",
                                      "sources": ["acs", "census_count"]}],
         "comparison": False, "message": ""}),
    "resolve_population_group": lambda a: {"status": "ok", "group": None},
    "resolve_geography": lambda a: {"status": "ok", "year": a["year"],
                                     "sql_filter": "LEFT({table}.\"CENSUS_BLOCK_GROUP\", 5) = '17031'"},
    "search_schema": lambda a: {"topics": ["employment"]},
    "get_schema": lambda a: {"topics": {}},
    "execute_query": lambda a: {"outcome": "answered", "rows": [{"rate": 0.071}],
                                "completeness": [], "truncated": False, "message": ""},
}


def _call(name: str, **args) -> types.Part:
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


class ScriptedLlm(BaseLlm):
    """Reacts to the latest tool response the way the instructions demand.

    It also records the system instruction it received.
    """

    model: str = "scripted"
    seen_instructions: list = []

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        self.seen_instructions.append(llm_request.config.system_instruction)
        last = llm_request.contents[-1]
        part = last.parts[0]
        if part.function_response is None:  # a new user message
            year = "2017" if "2017" in part.text else "2020"
            parts = [_call("resolve_year", text=year),
                     _call("resolve_population_group", text=None)]
        else:
            responses = {p.function_response.name: p.function_response.response
                         for p in last.parts}
            if "resolve_year" in responses:
                year = responses["resolve_year"]
                if year["status"] != "ok":
                    parts = [types.Part(text=year["message"])]
                else:
                    parts = [_call("resolve_geography", place="Cook County, Illinois",
                                   year=year["periods"][0]["year"])]
            elif "resolve_geography" in responses:
                parts = [_call("search_schema", query="unemployment rate", year=2020)]
            elif "search_schema" in responses:
                parts = [_call("get_schema", names=["employment"])]
            elif "get_schema" in responses:
                parts = [_call("execute_query", sql="SELECT 1")]
            else:
                parts = [types.Part(text="The unemployment rate is 7.1% (ACS 2016–2020).")]
        yield LlmResponse(content=types.Content(role="model", parts=parts))


CLARIFYING = {"resolve_year": {"ambiguous", "unavailable"},
              "resolve_population_group": {"ambiguous", "not_found"},
              "resolve_geography": {"ambiguous", "not_found", "unsupported", "no_data"}}


def check_tool_order(trace: list[tuple[int, str, dict]]) -> None:
    """Assert criteria 8 and 12 over a recorded (turn, tool, response) trace.

    8: resolve_geography is never called before resolve_year returned a final (ok)
       period in the conversation.
    12: execute_query is never called while a resolver's clarification is pending
       (its latest result in the conversation is not ok).
    """
    latest: dict[str, str] = {}
    for _turn, name, response in trace:
        if name == "resolve_geography":
            assert latest.get("resolve_year") == "ok", "resolve_geography before a final year"
        if name == "execute_query":
            pending = [n for n, s in latest.items() if s in CLARIFYING[n]]
            assert not pending, f"execute_query while clarification pending: {pending}"
        if name in CLARIFYING:
            latest[name] = response["status"]


def _run_conversation(messages: list[str]) -> tuple[list, ScriptedLlm, list[str]]:
    llm = ScriptedLlm(seen_instructions=[])
    trace: list[tuple[int, str, dict]] = []
    turn = {"n": 0}

    def intercept(tool, args, tool_context):
        response = CANNED[tool.name](args)
        trace.append((turn["n"], tool.name, response))
        return response

    test_agent = root_agent.model_copy(update={"model": llm, "before_tool_callback": intercept})
    runner = InMemoryRunner(agent=test_agent, app_name="census_compass")

    async def go():
        session = await runner.session_service.create_session(
            app_name="census_compass", user_id="u")
        texts = []
        for i, message in enumerate(messages):
            turn["n"] = i
            content = types.Content(role="user", parts=[types.Part(text=message)])
            async for event in runner.run_async(
                    user_id="u", session_id=session.id, new_message=content):
                if event.content and event.content.parts and event.content.parts[0].text:
                    texts.append(event.content.parts[0].text)
        return texts

    texts = asyncio.run(go())
    return trace, llm, texts


def test_scripted_run_clarifies_then_answers_in_order():
    trace, llm, texts = _run_conversation([
        "What is the unemployment rate in Cook County, Illinois in 2017?",
        "Use 2020",
    ])
    names = [(t, n) for t, n, _ in trace]
    assert names == [
        (0, "resolve_year"), (0, "resolve_population_group"),
        (1, "resolve_year"), (1, "resolve_population_group"), (1, "resolve_geography"),
        (1, "search_schema"), (1, "get_schema"), (1, "execute_query"),
    ]
    check_tool_order(trace)
    assert any("2015–2019" in t for t in texts)
    assert "7.1%" in texts[-1]
    # The literal {table} placeholder reached the model unchanged (no state templating).
    assert llm.seen_instructions and all("{table}" in s for s in llm.seen_instructions)


def test_check_tool_order_catches_violations():
    ok = {"status": "ok"}
    with pytest.raises(AssertionError, match="before a final year"):
        check_tool_order([(0, "resolve_geography", ok)])
    with pytest.raises(AssertionError, match="before a final year"):
        check_tool_order([(0, "resolve_year", {"status": "ambiguous"}),
                          (0, "resolve_geography", ok)])
    with pytest.raises(AssertionError, match="clarification pending"):
        check_tool_order([(0, "resolve_year", ok),
                          (0, "resolve_geography", {"status": "not_found"}),
                          (0, "execute_query", {"outcome": "answered"})])


def test_model_error_handled_end_to_end():
    class FailingLlm(BaseLlm):
        model: str = "failing"

        async def generate_content_async(self, llm_request, stream=False):
            raise RuntimeError("litellm.AuthenticationError: bad key sk-ant-xyz")
            yield  # pragma: no cover

    test_agent = root_agent.model_copy(update={"model": FailingLlm()})
    runner = InMemoryRunner(agent=test_agent, app_name="census_compass")

    async def go():
        session = await runner.session_service.create_session(
            app_name="census_compass", user_id="u")
        content = types.Content(role="user", parts=[types.Part(text="hi")])
        return [e async for e in runner.run_async(
            user_id="u", session_id=session.id, new_message=content)]

    events = asyncio.run(go())
    texts = [p.text for e in events if e.content for p in e.content.parts if p.text]
    assert texts == [MODEL_FAILURE_MESSAGE]
    assert "sk-ant" not in json.dumps([e.model_dump(mode="json") for e in events])



def test_adk_web_discovers_root_agent_from_repo_root():
    from pathlib import Path

    from google.adk.cli.utils.agent_loader import AgentLoader

    loader = AgentLoader(str(Path(__file__).resolve().parents[2]))
    assert "census_compass" in loader.list_agents()
    assert loader.load_agent("census_compass") is root_agent
