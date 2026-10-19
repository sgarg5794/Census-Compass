"""ADK agent definition: Claude via LiteLLM, the six tools and the system prompt.

`adk web` run from the repository root discovers `root_agent` through the
`census_compass` package. Importing this module makes no model, LanceDB or
Snowflake calls; the tools open those lazily.
"""

import logging
import time
from importlib.resources import files

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.callback_context import CallbackContext
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.models.lite_llm import LiteLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from census_compass.config import get_settings
from census_compass.guardrails.contracts import ROUTE_STATE_KEY
from census_compass.guardrails.gate import guardrail_callback, routing_hint
from census_compass.tools.geography import resolve_geography
from census_compass.tools.population_group import resolve_population_group
from census_compass.tools.query import DEADLINE_KEY, REJECTIONS_KEY, execute_query
from census_compass.tools.schema import get_schema, search_schema
from census_compass.tools.year import resolve_year

# LiteLLM reads ANTHROPIC_API_KEY from the environment; .env supplies it locally.
load_dotenv()

logger = logging.getLogger(__name__)

MODEL_FAILURE_MESSAGE = (
    "Sorry, the language model service failed while answering. Please try again later."
)

_PROMPTS = files("census_compass") / "prompts"
INSTRUCTION = (
    (_PROMPTS / "agent_instructions.md").read_text(encoding="utf-8")
    + "\n\n"
    + (_PROMPTS / "domain_rules.md").read_text(encoding="utf-8")
)


def instruction(ctx: ReadonlyContext) -> str:
    """Instruction provider: ADK skips `{state}` templating for providers, so the
    literal `{table}` placeholder in the domain rules reaches the model unchanged."""
    hint = routing_hint(ctx.state.get(ROUTE_STATE_KEY))
    return INSTRUCTION + "\n\n" + hint if hint else INSTRUCTION


def start_turn(callback_context: CallbackContext) -> None:
    """Start the per-question budget and SQL rejection count (invocation-scoped `temp:` state)."""
    callback_context.state[DEADLINE_KEY] = (
        time.monotonic() + get_settings().request_budget_seconds
    )
    callback_context.state[REJECTIONS_KEY] = 0
    return None


def on_model_error(
    callback_context: CallbackContext, llm_request: LlmRequest, error: Exception
) -> LlmResponse:
    """Replace a provider error with the model_failure outcome; never echo the error."""
    logger.warning("Model call failed: %s", type(error).__name__)
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=MODEL_FAILURE_MESSAGE)])
    )


root_agent = LlmAgent(
    name="census_compass",
    description="Answers questions about US demographics from Census data.",
    model=LiteLlm(model=get_settings().agent_model),
    instruction=instruction,
    tools=[
        resolve_year,
        resolve_population_group,
        resolve_geography,
        search_schema,
        get_schema,
        execute_query,
    ],
    before_agent_callback=[start_turn, guardrail_callback],
    on_model_error_callback=on_model_error,
)
