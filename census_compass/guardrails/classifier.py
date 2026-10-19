"""Guardrail classifier: one no-tools model call that labels a user message."""

import asyncio
import json
import logging
from pathlib import Path

from pydantic import ValidationError

from census_compass.config import Settings, get_settings
from census_compass.guardrails.contracts import ContextTurn, Decision

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "guardrail_classifier.md"
MAX_TURN_CHARS = 500


def _first_json_object(raw: str) -> dict | None:
    decoder = json.JSONDecoder()
    start = raw.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(raw, start)
        except ValueError:
            start = raw.find("{", start + 1)
            continue
        return obj if isinstance(obj, dict) else None
    return None


def parse_decision(raw: str) -> Decision | None:
    if not isinstance(raw, str):
        return None
    obj = _first_json_object(raw)
    if obj is None:
        return None
    try:
        decision = Decision.model_validate(obj)
    except ValidationError:
        return None
    if decision.label == "mixed" and not (decision.census_part or "").strip():
        return Decision(label="off_topic")
    return decision


def _escape(text: str) -> str:
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _user_content(message: str, context: list[ContextTurn], turns: int) -> str:
    recent = context[-turns:] if turns > 0 else []
    history = "".join(
        f'<turn role="{t.role}">{_escape(t.text[:MAX_TURN_CHARS])}</turn>' for t in recent
    )
    return f"<history>{history}</history>\n<message>{_escape(message)}</message>"


async def classify(
    message: str,
    context: list[ContextTurn],
    *,
    settings: Settings | None = None,
    completion=None,
    timeout: float | None = None,
) -> Decision | None:
    """Label a message. Never raises; any failure or invalid output returns None."""
    try:
        settings = settings or get_settings()
        if completion is None:
            import litellm

            completion = litellm.acompletion
        if timeout is None:
            timeout = settings.guardrail_timeout_seconds
        messages = [
            {"role": "system", "content": PROMPT_PATH.read_text(encoding="utf-8")},
            {
                "role": "user",
                "content": _user_content(message, context, settings.guardrail_context_turns),
            },
        ]
        resp = await asyncio.wait_for(
            completion(
                model=settings.guardrail_model,
                messages=messages,
                max_tokens=200,
                temperature=0,
                timeout=timeout,
            ),
            timeout,
        )
        return parse_decision(resp.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001 - fail-open by design
        logger.warning("guardrail classifier failed: %s", type(exc).__name__)
        return None
