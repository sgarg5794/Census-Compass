from pathlib import Path

import pytest
from pydantic import ValidationError

from census_compass.config import Settings
from census_compass.guardrails import replies
from census_compass.guardrails.contracts import (
    BLOCKING_LABELS,
    ROUTE_STATE_KEY,
    ContextTurn,
    Decision,
)

GUARDRAIL_KEYS = (
    "GUARDRAIL_MODEL",
    "GUARDRAIL_TIMEOUT_SECONDS",
    "MAX_INPUT_CHARS",
    "GUARDRAIL_CONTEXT_TURNS",
    "GLOSSARY_PATH",
)


def _check_defaults(s):
    assert s.guardrail_model == "anthropic/claude-haiku-4-5-20251001"
    assert s.guardrail_timeout_seconds == 5
    assert s.max_input_chars == 2000
    assert s.guardrail_context_turns == 3
    assert s.glossary_path == Path("definitions/source/census_glossary.yaml")


def test_settings_defaults(monkeypatch):
    for k in GUARDRAIL_KEYS:
        monkeypatch.delenv(k, raising=False)
    _check_defaults(Settings(_env_file=None, agent_model="x", embedding_model="y"))


def test_env_example_does_not_override_guardrail_defaults(tmp_path, monkeypatch):
    for k in GUARDRAIL_KEYS:
        monkeypatch.delenv(k, raising=False)
    src = Path(__file__).resolve().parents[2] / ".env.example"
    tmp = tmp_path / ".env"
    tmp.write_text(src.read_text())
    _check_defaults(Settings(_env_file=tmp))


def test_decision_rejects_unknown_label_and_extra_fields():
    assert Decision(label="data").census_part is None
    with pytest.raises(ValidationError):
        Decision(label="nope")
    with pytest.raises(ValidationError):
        Decision(label="data", extra_field=1)
    with pytest.raises(ValidationError):
        ContextTurn(role="system", text="x")
    with pytest.raises(ValidationError):
        ContextTurn(role="user", text="x", other=1)


def test_blocking_labels_exact():
    assert BLOCKING_LABELS == frozenset({"off_topic", "inappropriate", "manipulation"})
    assert ROUTE_STATE_KEY == "temp:guardrail_route"


def test_reply_texts_exact():
    assert replies.TOO_LONG == (
        "That message is too long for me to process. "
        "Please ask your Census question in under 2,000 characters."
    )
    assert replies.OFF_TOPIC == (
        "I can only help with US Census data — for example population, income, housing, "
        "education or employment for states and counties — and with explaining Census terms. "
        "What would you like to know?"
    )
    assert replies.MANIPULATION == (
        "I can't change how I work or share my instructions, but I'm happy to answer "
        "questions about US Census data."
    )
    assert replies.inappropriate_reply("X") == (
        "I can't help with that framing. I can share neutral facts from the Census data "
        "instead, for example: *X*."
    )
    assert replies.inappropriate_reply(None) == "I can't help with that framing."
    assert replies.inappropriate_reply("  ") == "I can't help with that framing."


def test_reply_for_rejects_passing_labels():
    assert replies.reply_for("off_topic") == replies.OFF_TOPIC
    assert replies.reply_for("manipulation") == replies.MANIPULATION
    assert replies.reply_for("inappropriate", "X") == replies.inappropriate_reply("X")
    for label in ("data", "general", "smalltalk", "mixed", "bogus"):
        with pytest.raises(ValueError):
            replies.reply_for(label)
