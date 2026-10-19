"""Shared contracts for the guardrails."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

Label = Literal[
    "data", "general", "smalltalk", "mixed", "off_topic", "inappropriate", "manipulation"
]
BLOCKING_LABELS: frozenset[str] = frozenset({"off_topic", "inappropriate", "manipulation"})
ROUTE_STATE_KEY = "temp:guardrail_route"


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: Label
    census_part: str | None = None
    neutral_offer: str | None = None


class ContextTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    text: str
