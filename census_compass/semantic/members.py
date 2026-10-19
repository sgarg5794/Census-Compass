"""Convert v1 per-table schemas into LLM-facing members (spec sections 4.1-4.4 and 6.4).

One call converts one Census group (and, for a base code, its race versions A-I) into
dimensions, measures and the builder metadata kept beside them. Rules:

- Classification (section 4.2): a group title containing Median, Quartile, Per Capita,
  Average, Gini or Ratio is a published statistic -> dimension (`type: number`); counts and
  "Aggregate ..." totals -> measure (`type: sum`). "Ratio Of Income To Poverty Level" groups
  are counts of people per ratio bracket, so they stay measures.
- Margins of error are never members; they become `meta.moe_sql` of their estimate.
- Published per capita income and average household size are named `block_group_<v1 name>`.
- Names reuse the v1 logical names; E (Native Hawaiian and Other Pacific Islander alone)
  columns, which the database dictionary does not document, get names, descriptions and
  universes the way v1 builds them for the other race versions, using Census API labels.
- Format: plain counts are `number`. Other statistics: `percent` if the title mentions a
  percentage, `minutes` for travel time in minutes, `years` for median age, `currency` if
  the title or universe mentions dollars, income, earnings, rent, value, price or costs,
  otherwise `number`.
"""

import re
from typing import Literal

from census_compass.contracts import Dimension, Measure, MemberMetadata
from census_compass.semantic.definitions import measure_name
from census_compass.semantic.race_groups import RACE_GROUPS, race_group

_DIMENSION_WORDS = ("median", "quartile", "per capita", "average", "gini", "ratio")
_COUNT_TITLES = ("ratio of income to poverty level",)
_BLOCK_GROUP_ONLY = {"B19301", "B25010"}
_MONEY = re.compile(r"\b(dollars|income|earnings|rent|value|price|costs)\b", re.IGNORECASE)
_RACE_CODES = "".join(g.code for g in RACE_GROUPS)
_BASE = re.compile(r"[BC]\d{5}")
_RACE_VERSION = re.compile(rf"([BC]\d{{5}})([{_RACE_CODES}])")
_STANDALONE = re.compile(r"[BC]\d{5}PR|P\d{3}")  # Puerto Rico versions; redistricting
_E_COLUMN = re.compile(r"([BC]\d{5})E([em])(\d+)")

_KEY_DESCRIPTION = "Census block group identifier; one row per block group."


def classify_group(group_title: str) -> Literal["sum", "dimension"]:
    title = group_title.lower()
    if title.startswith(_COUNT_TITLES):
        return "sum"
    return "dimension" if any(word in title for word in _DIMENSION_WORDS) else "sum"


def describe(group_title: str, universe: str, path: list[str], race_code: str | None) -> str:
    text = f"{group_title.rstrip('.')}. Universe: {universe.rstrip('.')}. {' > '.join(path)}"
    if race_code:
        group = race_group(race_code)
        also = f"; also called {', '.join(group.synonyms)}" if group.synonyms else ""
        text += f" ({group.label}{also})"
    return text + "."


def _format(group_title: str, universe: str, classification: str) -> str:
    title = group_title.lower()
    if classification == "sum" and not title.startswith("aggregate"):
        return "number"
    if "percentage" in title:
        return "percent"
    if "minutes" in title:
        return "minutes"
    if title.startswith("median age"):
        return "years"
    if _MONEY.search(group_title) or _MONEY.search(universe):
        return "currency"
    return "number"


def _physical(v1_table: dict, year: int) -> str:
    physical = v1_table["metadata"]["physical_table"]
    if not physical.startswith(f"{year}_"):
        raise ValueError(f"Table {physical} is not a {year} table")
    return physical


def _qualified(physical: str, column: str) -> str:
    return f'"{physical}"."{column}"'


def _group_of(field: dict) -> str:
    source = field["metadata"]["source_metadata"] or {}
    return source.get("TABLE_NUMBER") or source["COLUMN_ID"][:4]


def _clean(part: str) -> str:
    return part.strip().rstrip(":").removesuffix(" --").strip()


def _normalized(text: str) -> str:
    return " ".join(text.lower().rstrip(":").split())


def _breakdown(group_title: str, universe: str, path: list[str]) -> list[str]:
    """Breakdown path without pure restatements, every other segment kept in order.

    Dropped: "Total" segments, and leading segments exactly equal (after normalising case,
    whitespace and a trailing colon) to the group title, the title without its race
    parenthetical, or the universe. Never dropped by substring containment.
    """
    base_title = re.sub(r"\s*\([^()]*\)$", "", group_title)
    restatements = {_normalized(group_title), _normalized(base_title), _normalized(universe)}
    parts = [p for p in (_clean(p) for p in path) if p and _normalized(p) != "total"]
    while parts and _normalized(parts[0]) in restatements:
        parts.pop(0)
    return parts or ["Total"]


def _with_race(text: str, label: str) -> str:
    """Swap the White alone label of an A-version text for another race label."""
    def swap(match: re.Match) -> str:
        return label.title() if match.group(0)[6].isupper() else label
    return re.sub(r"white alone", swap, text, count=1, flags=re.IGNORECASE)


def _row(physical, column, name, group_title, universe, path, race, moe, classification,
         definition_source, heading=None):
    breakdown = _breakdown(group_title, universe, path)
    meta = {"universe": universe}
    if moe:
        meta["moe_sql"] = _qualified(physical, moe)
    if race:
        meta["population_group"] = race
    member = {
        "name": name, "title": f"{heading or group_title}: {' > '.join(breakdown)}",
        "description": describe(group_title, universe, breakdown, race),
        "sql": _qualified(physical, column),
        "format": _format(group_title, universe, classification), "meta": meta,
    }
    dimension = classification == "dimension"
    built = Dimension(type="number", **member) if dimension else Measure(type="sum", **member)
    metadata = MemberMetadata(
        file="", member=name, kind="dimension" if dimension else "measure", table=physical,
        source_column=column, aggregation="none" if dimension else "sum",
        definition_source=definition_source, population_group=race)
    return built, metadata


def _shared_titles(v1_table: dict) -> set[str]:
    """Group titles the Census uses for more than one group in this table (e.g. B28003 and
    B28008, households vs people); their member titles also name the universe."""
    groups: dict[str, set[str]] = {}
    for field in v1_table["measures"].values():
        title = (field["metadata"]["source_metadata"] or {}).get("TABLE_TITLE")
        if title:
            groups.setdefault(title, set()).add(_group_of(field))
    return {title for title, codes in groups.items() if len(codes) > 1}


def _documented(v1_table: dict, physical: str, version: str, race: str | None,
                prefix: str) -> list[tuple]:
    rows = []
    shared = _shared_titles(v1_table)
    for v1_name, field in v1_table["measures"].items():
        if _group_of(field) != version:
            continue
        column = field["metadata"]["source_column"]
        if field["sql"] != f'"{column}"':
            raise ValueError(f"Unexpected v1 sql for {column}: {field['sql']}")
        source = field["metadata"]["source_metadata"]
        if "TABLE_TITLE" in source:
            title, universe = source["TABLE_TITLE"], source["TABLE_UNIVERSE"]
            path = field["metadata"]["field_path"][3:]
        else:  # redistricting dictionary rows are flat
            title, universe = source["COLUMN_TOPIC"].title(), source["COLUMN_UNIVERSE"]
            path = [source["FIELD_NAME"]]
        moe = (field.get("margin_of_error") or {}).get("metadata", {}).get("source_column")
        classification = classify_group(title)
        heading = f"{title} ({universe})" if title in shared else None
        rows.append(_row(physical, column, prefix + v1_name, title, universe, path, race, moe,
                         classification, "dictionary", heading))
    return rows


def _e_columns(v1_table: dict, base: str) -> list[tuple[str, int]]:
    found = []
    for column in v1_table["unresolved_columns"]:
        match = _E_COLUMN.fullmatch(column)
        if match and match[1] == base and match[2] == "e":
            found.append((column, int(match[3])))
    return sorted(found, key=lambda item: item[1])


def _undocumented_e(v1_table: dict, physical: str, base: str, api_labels: dict[str, str],
                    prefix: str) -> list[tuple]:
    """E race version: the same structure as the documented A version, with API labels."""
    columns = _e_columns(v1_table, base)
    if not columns:
        return []
    a_fields = {f["metadata"]["source_column"]: f for f in v1_table["measures"].values()
                if _group_of(f) == base + "A"}
    label = race_group("E").label
    rows = []
    for column, number in columns:
        sibling = a_fields.get(f"{base}Ae{number}")
        if sibling is None:
            raise ValueError(f"No documented White alone version for {column}")
        source = sibling["metadata"]["source_metadata"]
        title = _with_race(source["TABLE_TITLE"], label)
        universe = _with_race(source["TABLE_UNIVERSE"], label)
        if column in api_labels:
            path = api_labels[column].split("!!")
        else:  # Census API labels match the A version column-for-column (148/148 verified)
            path = sibling["metadata"]["field_path"][3:]
        path = [_clean(p) for p in path]
        field_path = ["Estimate", title.upper(), universe, *path]
        name = measure_name(column, {"field_path": field_path,
                                     "source_metadata": {"TABLE_TITLE": title}})
        moe = f"{base}Em{number}"
        moe = moe if moe in v1_table["unresolved_columns"] else None
        rows.append(_row(physical, column, prefix + name, title, universe, path, "E", moe,
                         classify_group(title), "census_api"))
    return rows


def convert_group(v1_table: dict, group: str, year: int, api_labels: dict[str, str]
                  ) -> tuple[list[Dimension], list[Measure], list[MemberMetadata]]:
    """Convert one group (a base code converts its race versions A-I too).

    Metadata is returned dimensions first, then measures, in member order.
    """
    physical = _physical(v1_table, year)
    if _BASE.fullmatch(group):
        base, versions = group, [(group, None), *((group + c, c) for c in _RACE_CODES)]
    elif match := _RACE_VERSION.fullmatch(group):
        base, versions = match[1], [(group, match[2])]
    elif _STANDALONE.fullmatch(group):
        base, versions = group, [(group, None)]
    else:
        raise ValueError(f"Unrecognised group code: {group}")
    prefix = "block_group_" if base in _BLOCK_GROUP_ONLY else ""
    rows = []
    for version, race in versions:
        if race == "E":
            rows += _undocumented_e(v1_table, physical, base, api_labels, prefix)
        else:
            rows += _documented(v1_table, physical, version, race, prefix)
    if not rows:
        raise ValueError(f"Group {group} has no columns in {physical}")
    dims = [(m, x) for m, x in rows if isinstance(m, Dimension)]
    measures = [(m, x) for m, x in rows if isinstance(m, Measure)]
    return ([m for m, _ in dims], [m for m, _ in measures],
            [x for _, x in dims] + [x for _, x in measures])


def key_dimension(v1_table: dict, year: int) -> tuple[Dimension, MemberMetadata]:
    physical = _physical(v1_table, year)
    dimension = Dimension(
        name="census_block_group", title="Census block group", description=_KEY_DESCRIPTION,
        sql=_qualified(physical, "CENSUS_BLOCK_GROUP"), type="string", primary_key=True)
    metadata = MemberMetadata(
        file="", member="census_block_group", kind="dimension", table=physical,
        source_column="CENSUS_BLOCK_GROUP", aggregation="none", definition_source="project")
    return dimension, metadata


def classification_report(v1_tables: list[dict]) -> list[tuple[str, str, str]]:
    """(group, title, classification) for every group, including undocumented E versions."""
    titles: dict[str, str] = {}
    for table in v1_tables:
        for field in table["measures"].values():
            source = field["metadata"]["source_metadata"]
            title = source.get("TABLE_TITLE") or source["COLUMN_TOPIC"].title()
            titles.setdefault(_group_of(field), title)
        for column in table["unresolved_columns"]:
            match = _E_COLUMN.fullmatch(column)
            if match and match[1] + "A" in titles:
                titles.setdefault(match[1] + "E",
                                  _with_race(titles[match[1] + "A"], race_group("E").label))
    return [(group, title, classify_group(title)) for group, title in titles.items()]
