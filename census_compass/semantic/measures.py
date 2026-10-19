"""Hand-written measures (spec sections 4.3, 4.4, 6.1 and 6.2).

Templates in `definitions/source/measures.yaml` name bare column codes. For a year the builder
finds each column's physical table, renders the measure SQL with one validity predicate per
block group (every numerator and denominator input present), adds a one-to-one join on
CENSUS_BLOCK_GROUP for any table other than the topic's own, and keeps the coverage SQL,
input columns, normalized SQL and Subject Definitions page in the builder metadata only.

    value    = scale * SUM(IFF(<pred>, NULL, <num>)) / NULLIF(SUM(IFF(<pred>, NULL, <den>)), 0)
    coverage = SUM(IFF(<pred>, NULL, <den>)) / NULLIF(SUM(<den>), 0)

`<pred>` ORs an IS NULL check for every input column once (numerator columns first, then
denominator columns, in template order). With scale 1 the multiplication is omitted.
"""

import re
from dataclasses import dataclass, replace
from functools import cache
from pathlib import Path

import sqlglot
import yaml

from census_compass.contracts import Join, Measure, MemberMetadata
from census_compass.semantic.race_groups import RaceGroup
from census_compass.semantic.sources import TopicSpec, load_topics

TOPICS_PATH = Path("definitions/source/topics.yaml")
SPARSE_NOTE = ("The Census Bureau does not publish this for every block group; block groups "
               "without it are left out.")

_ESTIMATE = re.compile(r"([BC]\d{5})(?:[A-I]|PR)?e\d+")
_DEMOGRAPHIC_TABLE = re.compile(r"(\d{4})_CBG_[BC]\d{2}")
_EXCLUDED_TABLES = {"B99"}  # allocation flags; excluded from the mapping (spec section 5)
_KEY = "CENSUS_BLOCK_GROUP"


@dataclass(frozen=True)
class MeasureTemplate:
    name: str
    topic: str
    title: str
    description: str
    numerator: list[str]
    denominator: list[str]
    scale: int
    format: str
    universe: str
    page: str
    for_each: str | None = None
    sparse: bool = False
    population_group: str | None = None


def load_templates(path: Path) -> list[MeasureTemplate]:
    entries = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    templates = []
    for entry in entries:
        template = MeasureTemplate(
            name=entry["name"], topic=entry["topic"], title=entry["title"],
            description=" ".join(entry["description"].split()),
            numerator=[str(c) for c in entry["numerator"]],
            denominator=[str(c) for c in entry["denominator"]],
            scale=entry["scale"], format=entry["format"], universe=entry["universe"],
            page=str(entry["page"]), for_each=entry.get("for_each"),
            sparse=bool(entry.get("sparse", False)))
        if template.for_each not in (None, "race_groups"):
            raise ValueError(f"{template.name}: unknown for_each {template.for_each!r}")
        if not template.numerator or not template.denominator:
            raise ValueError(f"{template.name}: numerator and denominator are required")
        templates.append(template)
    return templates


def _race_text(text: str, group: RaceGroup) -> str:
    return text.replace("{slug}", group.slug).replace("{label}", group.label)


def expand(templates: list[MeasureTemplate], race_groups) -> list[MeasureTemplate]:
    """One explicit measure per race group for `for_each: race_groups` templates."""
    expanded = []
    for template in templates:
        if template.for_each is None:
            expanded.append(template)
            continue
        for group in race_groups:
            description = _race_text(template.description, group)
            if group.synonyms:
                description += f" {group.label} is also called {', '.join(group.synonyms)}."
            expanded.append(replace(
                template, name=_race_text(template.name, group),
                title=_race_text(template.title, group), description=description,
                universe=_race_text(template.universe, group),
                numerator=[c.replace("{race}", group.code) for c in template.numerator],
                denominator=[c.replace("{race}", group.code) for c in template.denominator],
                for_each=None, population_group=group.code))
    return expanded


@cache
def _column_tables(year: int, tables_dir: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(tables_dir.glob(f"{year}_CBG_*.yaml")):
        if not _DEMOGRAPHIC_TABLE.fullmatch(path.stem) or path.stem[-3:] in _EXCLUDED_TABLES:
            continue
        table = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)
        physical = table["metadata"]["physical_table"]
        columns = [f["metadata"]["source_column"] for f in table["measures"].values()]
        columns += list(table.get("unresolved_columns") or [])
        for column in columns:
            if not _ESTIMATE.fullmatch(column):
                continue
            if result.setdefault(column, physical) != physical:
                raise ValueError(f"Column {column} is in {result[column]} and {physical}")
    return result


def column_tables(year: int, tables_dir: Path = Path("data/tables")) -> dict[str, str]:
    """Map every estimate column code of the year's demographic tables to its physical table
    (from the v1 catalog, including undocumented E columns)."""
    return dict(_column_tables(year, Path(tables_dir)))


@cache
def _topics(path: Path) -> tuple[TopicSpec, ...]:
    return tuple(load_topics(path))


def _quoted(table: str, column: str) -> str:
    return f'"{table}"."{column}"'


def _expression(terms: list[str], tables: dict[str, str]) -> str:
    parts = []
    for index, term in enumerate(terms):
        column = term.lstrip("-")
        sign = "-" if term.startswith("-") else "+"
        ref = _quoted(tables[column], column)
        parts.append(("-" + ref if sign == "-" else ref) if index == 0 else f"{sign} {ref}")
    return " ".join(parts)


def render(template: MeasureTemplate, year: int, column_tables: dict[str, str],
           topics: list[TopicSpec] | None = None
           ) -> tuple[Measure, list[Join], MemberMetadata]:
    if template.for_each is not None:
        raise ValueError(f"{template.name}: expand for_each templates before rendering")
    topic_list = list(topics) if topics is not None else list(_topics(TOPICS_PATH))
    by_name = {t.name: t for t in topic_list}
    by_group = {g: t for t in topic_list for g in t.groups}
    if template.topic not in by_name:
        raise ValueError(f"{template.name}: unknown topic {template.topic}")
    own = f"{year}_{by_name[template.topic].table}"

    inputs = list(dict.fromkeys(c.lstrip("-") for c in [*template.numerator,
                                                       *template.denominator]))
    missing = [c for c in inputs if c not in column_tables]
    if missing:
        raise ValueError(f"{template.name}: no {year} table has {', '.join(missing)}")

    joins: list[Join] = []
    for column in inputs:
        table = column_tables[column]
        if table == own or any(f'"{table}".' in j.sql for j in joins):
            continue
        match = _ESTIMATE.fullmatch(column)
        topic = by_group.get(match[1]) if match else None
        if topic is None or f"{year}_{topic.table}" != table:
            raise ValueError(f"{template.name}: no topic reads {column} from {table}")
        joins.append(Join(name=f"{topic.name}_{year}", relationship="one_to_one",
                          sql=f"{_quoted(own, _KEY)} = {_quoted(table, _KEY)}"))

    pred = " OR ".join(f"{_quoted(column_tables[c], c)} IS NULL" for c in inputs)
    num = _expression(template.numerator, column_tables)
    den = _expression(template.denominator, column_tables)
    valid_den = f"SUM(IFF({pred}, NULL, {den}))"
    sql = f"SUM(IFF({pred}, NULL, {num})) / NULLIF({valid_den}, 0)"
    if template.scale != 1:
        sql = f"{template.scale} * {sql}"
    coverage = f"{valid_den} / NULLIF(SUM({den}), 0)"

    description = template.description
    if template.sparse:
        description += " " + SPARSE_NOTE
    meta = {"universe": template.universe}
    if template.population_group:
        meta["population_group"] = template.population_group
    measure = Measure(name=template.name, title=template.title, description=description,
                      sql=sql, type="number", format=template.format, meta=meta)

    def qualified(terms: list[str]) -> list[str]:
        columns = dict.fromkeys(t.lstrip("-") for t in terms)
        return [f"{column_tables[c]}.{c}" for c in columns]

    metadata = MemberMetadata(
        file="", member=template.name, kind="measure", table=own, source_column=None,
        aggregation="formula", definition_source="hand_written",
        subject_definitions_page=template.page,
        normalized_sql=sqlglot.parse_one(sql, read="snowflake").sql(dialect="snowflake"),
        numerator_columns=qualified(template.numerator),
        denominator_columns=qualified(template.denominator),
        coverage_sql=coverage, population_group=template.population_group)
    return measure, joins, metadata
