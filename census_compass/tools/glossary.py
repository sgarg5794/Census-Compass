"""Glossary tool: answers general Census concept questions from a reviewed file only."""

import logging
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from census_compass.config import get_settings

logger = logging.getLogger(__name__)

MAX_ENTRIES = 3
MAX_SENTENCES = 4
ALLOWED_URL_PREFIXES = ("https://www2.census.gov/", "https://www.census.gov/")
NO_MATCH_MESSAGE = "No entry in the Census reference material matches this question."
UNAVAILABLE_MESSAGE = "The Census reference material is unavailable."


class GlossarySource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    page: int | str
    url: str


class GlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term: str
    aliases: list[str] = []
    definition: str
    source: GlossarySource


_cache: dict[tuple[Path, int], list[GlossaryEntry]] = {}


def load_glossary(path: Path | None = None) -> list[GlossaryEntry]:
    """Load and parse the glossary file; raises on a missing or invalid file."""
    resolved = Path(path if path is not None else get_settings().glossary_path).resolve()
    key = (resolved, resolved.stat().st_mtime_ns)
    if key not in _cache:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            raise ValueError("glossary file must contain a list of entries")
        entries = [GlossaryEntry.model_validate(row) for row in raw]
        for stale in [k for k in _cache if k[0] == resolved]:
            del _cache[stale]
        _cache[key] = entries
    return _cache[key]


def _sentence_count(text: str) -> int:
    return len([p for p in re.split(r"[.!?]+(?:\s+|$)", text) if p.strip()])


def validate_glossary(entries: list[GlossaryEntry]) -> list[str]:
    """Return one error string per problem found; an empty list means valid."""
    errors: list[str] = []
    seen: dict[str, str] = {}
    for entry in entries:
        for phrase in [entry.term, *entry.aliases]:
            key = phrase.casefold().strip()
            if key in seen:
                errors.append(
                    f"{entry.term}: duplicate term or alias '{phrase}' (also in {seen[key]})"
                )
            else:
                seen[key] = entry.term
        if not entry.definition.strip():
            errors.append(f"{entry.term}: blank definition")
        elif _sentence_count(entry.definition) > MAX_SENTENCES:
            errors.append(f"{entry.term}: definition longer than {MAX_SENTENCES} sentences")
        src = entry.source
        if not src.title.strip():
            errors.append(f"{entry.term}: source title missing")
        if not str(src.page).strip():
            errors.append(f"{entry.term}: source page missing")
        if not src.url.strip():
            errors.append(f"{entry.term}: source url missing")
        elif not src.url.startswith(ALLOWED_URL_PREFIXES):
            errors.append(
                f"{entry.term}: source url must start with {' or '.join(ALLOWED_URL_PREFIXES)}"
            )
    return errors


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^\w%]+", " ", text.casefold().replace("_", " ")).split())


def lookup_glossary(question: str) -> dict:
    """Look up Census concept and method definitions in the reviewed Census glossary.

    Call this for general questions about Census concepts, terms and methods (for
    example "what is a margin of error?" or "what does median mean?"). Do not use it
    for data questions about places or measures; those use the data tools.

    Answer ONLY from the returned entries, and name each source you use by its title
    and page. If "entries" is empty, say "That isn't covered in my Census reference
    material." and stop: never answer a Census concept question from memory.

    Args:
        question: The user's concept question, or the term they asked about.

    Returns:
        {"entries": [{"term", "definition", "source": {"title", "page", "url"}}],
        "message": str}. At most 3 entries; "message" explains an empty result.
    """
    try:
        entries = load_glossary()
    except Exception as exc:  # noqa: BLE001 - tool must never raise
        logger.error("Glossary unavailable: %s", type(exc).__name__)
        return {"entries": [], "message": UNAVAILABLE_MESSAGE}

    padded = f" {_normalize(question)} "
    matches: list[tuple[int, int, GlossaryEntry]] = []
    for index, entry in enumerate(entries):
        best = 0
        for phrase in [entry.term, *entry.aliases]:
            norm = _normalize(phrase)
            if norm and f" {norm} " in padded:
                best = max(best, len(norm.split()))
        if best:
            matches.append((-best, index, entry))
    matches.sort(key=lambda m: (m[0], m[1]))
    results = [
        {
            "term": e.term,
            "definition": e.definition,
            "source": e.source.model_dump(),
        }
        for _, _, e in matches[:MAX_ENTRIES]
    ]
    if not results:
        return {"entries": [], "message": NO_MATCH_MESSAGE}
    return {"entries": results, "message": f"Found {len(results)} matching entries."}
