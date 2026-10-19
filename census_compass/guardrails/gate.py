"""Guardrail gate: a before_agent_callback that blocks off-topic, inappropriate and
manipulation messages with a fixed reply, and otherwise stores a routing hint.

Fails open: any classifier problem lets the agent run unchanged.
"""

import logging
import time

from google.adk.agents.callback_context import CallbackContext
from google.genai import types

from census_compass.config import get_settings
from census_compass.guardrails.contracts import (
    BLOCKING_LABELS,
    ROUTE_STATE_KEY,
    ContextTurn,
    Decision,
)
from census_compass.guardrails.replies import OFF_TOPIC, TOO_LONG, reply_for
from census_compass.tools.query import DEADLINE_KEY

logger = logging.getLogger(__name__)

_CONTEXT_TURN_CHARS = 500
_HINTS = {
    "general": "Routing hint: this is a Census concept question. "
    "Answer only from lookup_glossary results.",
    "smalltalk": "Routing hint: this is small talk. Reply briefly and do not call data tools.",
    "mixed": "Routing hint: this message mixes Census and non-Census requests. "
    "Answer only the Census part and add one line saying the rest was skipped.",
}


def is_too_long(text: str, max_chars: int) -> bool:
    return len(text) > max_chars


def routing_hint(label: str | None) -> str:
    return _HINTS.get(label or "", "")


def build_context(events, current_invocation_id: str, limit: int) -> list[ContextTurn]:
    """Last `limit` text turns of earlier invocations, oldest first."""
    if limit <= 0:
        return []
    turns: list[ContextTurn] = []
    for event in events or []:
        if event.invocation_id == current_invocation_id:
            continue
        parts = event.content.parts if event.content and event.content.parts else []
        text = "".join(p.text for p in parts if p.text)
        if not text.strip():
            continue
        role = "user" if event.author == "user" else "assistant"
        turns.append(ContextTurn(role=role, text=text[:_CONTEXT_TURN_CHARS]))
    return turns[-limit:]


async def classify_message(
    message: str, context: list[ContextTurn], *, timeout: float
) -> Decision | None:
    """Thin wrapper over the classifier; the seam tests monkeypatch."""
    from census_compass.guardrails.classifier import classify

    return await classify(message, context, settings=get_settings(), timeout=timeout)


def _reply(text: str) -> types.Content:
    return types.Content(role="model", parts=[types.Part(text=text)])


async def guardrail_callback(callback_context: CallbackContext) -> types.Content | None:
    try:
        settings = get_settings()
        content = callback_context.user_content
        parts = content.parts if content and content.parts else []
        text = "".join(p.text for p in parts if p.text)
        if not text.strip():
            return _reply(OFF_TOPIC)
        if is_too_long(text, settings.max_input_chars):
            return _reply(TOO_LONG)

        timeout = settings.guardrail_timeout_seconds
        deadline = callback_context.state.get(DEADLINE_KEY)
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.warning("Guardrail skipped: request budget exhausted")
                return None
            timeout = min(timeout, remaining)

        context = build_context(
            callback_context.session.events,
            callback_context.invocation_id,
            settings.guardrail_context_turns,
        )
        decision = await classify_message(text, context, timeout=timeout)
        if decision is None:
            logger.warning("Guardrail classifier gave no decision; failing open")
            return None
        if decision.label in BLOCKING_LABELS:
            logger.info("Guardrail blocked message: %s", decision.label)
            return _reply(reply_for(decision.label, decision.neutral_offer))
        if decision.census_part:
            logger.info("Guardrail census_part: %s", decision.census_part)
        callback_context.state[ROUTE_STATE_KEY] = decision.label
        return None
    except Exception as exc:  # fail-open: never refuse a legitimate question
        logger.warning("Guardrail failed open: %s", type(exc).__name__)
        return None
