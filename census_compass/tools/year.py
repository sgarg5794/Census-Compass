"""Year resolution tool: maps a user's year wording to a Census Compass data period."""

import re

from census_compass.contracts import Period, YearResolution

_YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
_LATEST_RE = re.compile(r"\b(latest|current|now|today|recent|most recent)\b", re.IGNORECASE)

_LABEL_RE = re.compile(
    r"(?<!\d)(?:(2015)\s*(?:–|—|-|to|through)\s*(2019)|(2016)\s*(?:–|—|-|to|through)\s*(2020))(?!\d)",
    re.IGNORECASE,
)
_LATEST_MESSAGE = (
    "This is the most recent data available (ACS 2016–2020 5-year estimates), not a current figure."
)

_PERIODS = {
    2019: Period(year=2019, label="2015–2019", sources=["acs"]),
    2020: Period(year=2020, label="2016–2020", sources=["acs", "census_count"]),
}


def _result(**kwargs) -> dict:
    return YearResolution(**kwargs).model_dump()


def resolve_year(text: str | None) -> dict:
    """Resolve the year a user asked about into the data period to query.

    Neighborhood (block group) data exists only as two 5-year ACS periods, never
    as single years. Call this before any query whenever the user mentions a year.

    Rules:
    - No year, or "latest", "current", "now", "today", "recent": use 2020
      (the 2016–2020 period). 2020 also has Census counts (source "census_count").
      The result carries a message: always relay it, saying this is the most recent
      data available (ACS 2016–2020 5-year estimates), not a current figure.
    - A stated period label ("2015–2019", "2015 to 2019") is that one period (2019);
      likewise "2016–2020" is the 2020 period.
    - 2019: the 2015–2019 period (source "acs" only).
    - 2020: the 2016–2020 period (sources "acs" and "census_count").
    - 2015: treated as 2019; the period covers 2015–2019, not 2015 alone. Tell the user.
    - 2016, 2017, 2018: status "ambiguous". Ask the user whether to use 2015–2019 or
      2016–2020. Do not pick one for them.
    - Any other year (before 2015 or after 2020): status "unavailable".
    - Two years that are both 2019 and 2020 (for example "2019 vs 2020"): a comparison.
      "periods" has 2019 first, then 2020, and "comparison" is true.

    Args:
        text: The user's wording about time, such as "2019", "latest" or "2019 vs 2020".
            May be None.

    Returns:
        A dict with "status" ("ok", "ambiguous" or "unavailable"), "periods" (each with
        "year", "label" and "sources"), "comparison" (bool) and "message" (a short
        sentence to relay to the user when the status is not a plain ok).
    """
    raw = text or ""
    # Explicit period labels ("2015–2019", "2016 to 2020") name one period each.
    label_years = {2019 if m.group(1) else 2020 for m in _LABEL_RE.finditer(raw)}
    rest = _LABEL_RE.sub(" ", raw)
    years = sorted({int(y) for y in _YEAR_RE.findall(rest)} | label_years)

    if not years:
        if raw.strip() and not _LATEST_RE.search(raw):
            return _result(
                status="ambiguous",
                message="I could not tell which year you mean. Please say 2019 "
                "(2015–2019) or 2020 (2016–2020).",
            )
        return _result(status="ok", periods=[_PERIODS[2020]], message=_LATEST_MESSAGE)

    if any(y < 2015 or y > 2020 for y in years):
        return _result(
            status="unavailable",
            message="Only 2015–2019 and 2016–2020 data are available for neighborhoods.",
        )
    if any(2016 <= y <= 2018 for y in years):
        return _result(
            status="ambiguous",
            message="Single-year data is not available for neighborhoods. Do you want "
            "the 2015–2019 or the 2016–2020 period?",
        )

    if len(years) == 1:
        year = years[0]
        if year == 2015:
            return _result(
                status="ok",
                periods=[_PERIODS[2019]],
                message="Data is available as the 2015–2019 period, not 2015 alone.",
            )
        return _result(status="ok", periods=[_PERIODS[year]])

    if years == [2019, 2020]:
        return _result(status="ok", periods=[_PERIODS[2019], _PERIODS[2020]], comparison=True)

    # Remaining case: 2015 together with 2019 and/or 2020.
    return _result(
        status="ambiguous",
        message="Single-year data is not available for neighborhoods. Do you want "
        "the 2015–2019 or the 2016–2020 period, or a comparison of the two?",
    )
