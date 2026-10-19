"""Validated user-facing contracts for the chat boundary."""

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from census_compass.contracts import Completeness, Outcome


class _ChatModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChatRequest(_ChatModel):
    visitor_id: str
    conversation_id: str
    request_id: str
    text: str

    @field_validator("text")
    @classmethod
    def text_must_contain_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class Evidence(_ChatModel):
    executed_sql: str | None
    source_label: str | None
    period_label: str | None
    limitations: list[str]


class ChatResult(_ChatModel):
    outcome: Outcome
    answer: str
    rows: list[dict]
    evidence: list[Evidence]
    completeness: list[Completeness]
    truncated: bool


StatusStage = Literal["understanding", "finding_data", "querying", "preparing_answer"]


class StatusEvent(_ChatModel):
    kind: Literal["status"] = "status"
    stage: StatusStage


class ResultEvent(_ChatModel):
    kind: Literal["result"] = "result"
    result: ChatResult


class ErrorEvent(_ChatModel):
    kind: Literal["error"] = "error"
    message: str


ChatEvent: TypeAlias = Annotated[
    StatusEvent | ResultEvent | ErrorEvent,
    Field(discriminator="kind"),
]

_CHAT_EVENT_ADAPTER = TypeAdapter(ChatEvent)


def parse_chat_event(value: object) -> ChatEvent:
    """Parse an event from a mapping while rejecting internal/unknown shapes."""
    return _CHAT_EVENT_ADAPTER.validate_python(value)
