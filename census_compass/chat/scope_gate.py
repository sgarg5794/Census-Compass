"""Conservative pre-agent gate for the current dataset-only chat experience."""

import re
from typing import Literal

ScopeRoute = Literal["dataset", "out_of_scope", "unsupported_general_census", "clarify"]

_BLOCKED = re.compile(
    r"\b(weather|forecast|write\s+(?:me\s+)?(?:code|a script)|python|javascript|"
    r"programming|generate\s+code|recipe|movie|football|stock price)\b",
    re.IGNORECASE,
)
_DATA = re.compile(
    r"\b(population|people|residents?|demographic|race|ethnic(?:ity)?|age|sex|"
    r"households?|income|poverty|employment|unemploy(?:ed|ment)|housing|rent(?:er)?|"
    r"owner|education|school|commut(?:e|ing)|disabilit(?:y|ies)|language|veteran|"
    r"county|counties|state|states|block group|census tract|zip code|zip-code)\b",
    re.IGNORECASE,
)
_DATA_INTENT = re.compile(
    r"\b(how many|how much|what (?:was|is|were|are)|show|compare|difference|"
    r"break(?:down)?|distribution|percentage|percent|rate|median|average|total|count|"
    r"number|highest|lowest|more|less|by state|by county)\b",
    re.IGNORECASE,
)
_GENERAL_CENSUS = re.compile(
    r"\b(how does|how do|explain|define|what is the|when did|why does|history of|"
    r"conduct the|methodology|survey design)\b",
    re.IGNORECASE,
)
_UNCLEAR = re.compile(r"\b(tell me about|what about|and for|how about)\b", re.IGNORECASE)


def classify_dataset_question(text: str, *, has_dataset_context: bool = False) -> ScopeRoute:
    """Allow clear data questions; reject mixed/off-topic/code and fail closed otherwise.

    General Census explanations need the approved-document retrieval path, which is not
    connected yet, so this gate returns a distinct safe limitation for that route.
    """
    if _BLOCKED.search(text):
        return "out_of_scope"
    if _GENERAL_CENSUS.search(text) and re.search(r"\bcensus\b", text, re.IGNORECASE):
        return "unsupported_general_census"
    if has_dataset_context and _UNCLEAR.search(text):
        return "dataset"
    if _UNCLEAR.search(text):
        return "clarify"
    if _DATA.search(text) and _DATA_INTENT.search(text):
        return "dataset"
    if _DATA.search(text):
        return "clarify"
    return "out_of_scope"
