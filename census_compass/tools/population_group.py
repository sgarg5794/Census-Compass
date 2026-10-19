"""Population group resolution tool: maps race/ethnicity wording to a group code (A–I)."""

import re

from census_compass.contracts import PopulationGroupResolution
from census_compass.semantic.race_groups import RACE_GROUPS, RaceGroup, race_group

_TRAILING = {"people", "persons", "person", "households", "householders", "residents", "population"}
_COMBINATION_RE = re.compile(r"\balone\s+or\s+in\s+combination\b")

# Wordings beyond each group's own label, slug and synonyms.
_EXTRA_ALIASES = {
    "I": ("latina",),
    "C": ("american indian", "alaska native"),
}
_WHITE_WORDS = {"white"}


def _normalize(text: str) -> str:
    s = re.sub(r"[-_,]+", " ", text.lower())
    s = re.sub(r"[^\w\s]", "", s)
    words = s.split()
    while words and (words[-1] in _TRAILING):
        words.pop()
    if words and words[-1] == "americans":
        words[-1] = "american"
    return " ".join(words)


def _build_index() -> dict[str, str]:
    index: dict[str, str] = {}
    for g in RACE_GROUPS:
        names = (g.label, g.slug, *g.synonyms, *_EXTRA_ALIASES.get(g.code, ()))
        for name in names:
            index[_normalize(name)] = g.code
    return index


_INDEX = _build_index()
_ALL_LABELS = [g.label for g in RACE_GROUPS]


def _result(**kwargs) -> dict:
    return PopulationGroupResolution(**kwargs).model_dump()


def _ok(group: RaceGroup) -> dict:
    return _result(status="ok", code=group.code, label=group.label)


def resolve_population_group(text: str | None) -> dict:
    """Resolve a race or ethnicity group the user named into a population group code.

    Call this whenever the user asks about a specific race or ethnic group. Pass the
    returned "code" (A–I) to search_schema(population_group=...). Only when a code is
    given does search_schema look at race-specific measures; without a code it searches
    all-population measures. If the user names no group, do not call this tool (or if
    you do, search without a population group).

    Rules:
    - "White" or "white people" is status "ambiguous": the candidates are
      "White alone" (A, includes Hispanic White people) and
      "White alone, not Hispanic or Latino" (H). Ask the user which they mean; do not
      pick one for them.
    - "Black" or "African American" is B; "Native American", "American Indian" or
      "Alaska Native" is C; "Asian" is D; "Native Hawaiian" or "Pacific Islander" is E;
      "Hispanic", "Latino", "Latina" or "Latinx" is I.
    - "... alone or in combination" is status "not_found": only the "alone" groups have
      race-specific measures in this data. The candidates hold the matching "alone"
      group; offer it to the user.
    - Empty text: status "not_found" with no candidates; search without a group.
    - Unrecognized text: status "not_found" with all nine group labels as candidates.
      Offer them to the user.

    Args:
        text: The user's wording for the group, such as "Black", "Hispanic households"
            or "Asian alone". May be None.

    Returns:
        A dict with "status" ("ok", "ambiguous" or "not_found"), "code" (A–I, set only
        when status is "ok"), "label", "candidates" (group labels to offer the user) and
        "message" (a short sentence to relay when the status is not ok).
    """
    raw = (text or "").strip()
    if not raw:
        return _result(
            status="not_found",
            message="No population group was given. Search without a population group.",
        )

    lowered = raw.lower()
    combination = bool(_COMBINATION_RE.search(lowered))
    norm = _normalize(_COMBINATION_RE.sub(" ", lowered) if combination else lowered)

    if combination:
        code = "A" if norm in _WHITE_WORDS else _INDEX.get(norm) or _INDEX.get(f"{norm} alone")
        candidates = [race_group(code).label] if code else []
        return _result(
            status="not_found",
            candidates=candidates,
            message="Only the 'alone' groups have race-specific measures in this data, "
            "so 'alone or in combination' is not available."
            + (f" Did you mean {candidates[0]}?" if candidates else ""),
        )

    if norm in _WHITE_WORDS:
        return _result(
            status="ambiguous",
            candidates=[race_group("A").label, race_group("H").label],
            message="Do you mean White alone (including Hispanic White people) or "
            "White alone, not Hispanic or Latino?",
        )

    code = _INDEX.get(norm)
    if code:
        return _ok(race_group(code))

    return _result(
        status="not_found",
        candidates=list(_ALL_LABELS),
        message=f"I could not match '{raw}' to a population group. Which of these do you mean?",
    )
