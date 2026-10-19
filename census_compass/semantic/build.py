"""Topic file generation and build checks (spec sections 4, 5 and 8).

For each year the builder turns every topic of `definitions/source/topics.yaml` into one
LLM-facing topic file (`<topic>_<year>`): the block-group key, the converted members of the
topic's groups (with their race versions) and the hand-written measures whose `topic:` is that
topic, with the joins they need. Beside the files it produces the builder metadata (provenance,
completeness inputs, every physical column of every table read) and the allowed-table list.

- Census-count topics (names ending in a year, e.g. `..._2020`) are built for that year only and
  are not suffixed again. A group absent in one year (2019 B28012) is skipped for that year; a
  group that exists in neither year is an error.
- Internal topics (`internal: true`: geography, land area) hold dimensions only, are written to
  `{year}/internal/` so the topic-file loader never returns them, and still count toward the
  builder metadata and the allowed tables.
- `run_checks` returns the build errors (spec section 5; empty list = pass). Topic size is only a
  warning (`size_warnings`): reviewed topics are never split (user decision).
"""

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from functools import cache
from pathlib import Path

import jsonschema
import yaml

from census_compass.config import Settings
from census_compass.contracts import (
    BuilderMetadata,
    Dimension,
    Join,
    Measure,
    MemberMetadata,
    TopicFile,
)
from census_compass.semantic.measures import column_tables, expand, load_templates, render
from census_compass.semantic.members import convert_group, key_dimension
from census_compass.semantic.race_groups import RACE_GROUPS
from census_compass.semantic.sources import TopicSpec, load_census_api_labels, load_topics

YEARS = (2019, 2020)
E_COLUMNS_PER_YEAR = 148
SCHEMA_DIR = Path("definitions/schema")
FORBIDDEN_IN_LLM_FILES = ("coverage_sql", "missing_sql", "definition_source",
                          "subject_definitions_page")
EXCLUDED_TABLES = ("CBG_PATTERNS", "RENT_PERCENTAGE_HOUSEHOLD_INCOME", "TOTAL_RENTAL_GEO",
                   "CBG_B99")

ACS_PERIOD = ("ACS 5-year estimates, survey answers collected {start}–{year}, for each Census "
              "block group: ")
COUNT_PERIOD = "Official 2020 Census count as of April 1, 2020, for each Census block group: "
COUNT_CLOSING = " The preferred source for 2020 population, race and Hispanic origin counts."
RACE_NOTE = " Some of these are also given by race and Hispanic origin."

_INCLUDED = re.compile(r"(\d{4})_(?:CBG_[BC]\d{2}|REDISTRICTING_CBG_DATA)")
_REF = re.compile(r'"([^"]+)"\."([^"]+)"')
_YEAR_SUFFIX = re.compile(r"_(\d{4})$")
_RACE_VERSION = re.compile(r"([BC]\d{5})[A-I]")
_E_ESTIMATE = re.compile(r"[BC]\d{5}Ee\d+")
_E_COLUMN = re.compile(r"[BC]\d{5}E[em]\d+")
_KEY = "CENSUS_BLOCK_GROUP"
_PROPER = ("United States", "Puerto Rico", "Hispanic", "Latino", "White", "Black",
           "African American", "American Indian", "Alaska Native", "Asian", "Native Hawaiian",
           "Pacific Islander", "SNAP", "SSI", "Social Security", "English")
# Wording repeated in many group titles; dropped from file summaries to keep them short.
_NOISE = re.compile(r"\s*\(in \d{4} inflation-adjusted dollars\)|\s+in the (?:past|last) 12 months")

# Internal files: (member name, physical column, title, description, type).
_INTERNAL_MEMBERS = {
    "geography": [
        ("state", "STATE", "State", "Two-letter postal abbreviation of the state.", "string"),
        ("state_fips", "STATE_FIPS", "State code",
         "Two-digit state code; the first 2 digits of a Census block group identifier.",
         "string"),
        ("county_fips", "COUNTY_FIPS", "County code",
         "Three-digit county code within the state; state code plus county code are the first "
         "5 digits of a Census block group identifier.", "string"),
        ("county", "COUNTY", "County", "County name, for example Cook County.", "string"),
    ],
    "land_area_and_location": [
        ("land_area", "AMOUNT_LAND", "Land area",
         "Land area of the block group as published (the source does not document the unit).",
         "number"),
        ("water_area", "AMOUNT_WATER", "Water area",
         "Water area of the block group as published (the source does not document the unit).",
         "number"),
        ("latitude", "LATITUDE", "Latitude", "Latitude of a point inside the block group.",
         "number"),
        ("longitude", "LONGITUDE", "Longitude", "Longitude of a point inside the block group.",
         "number"),
    ],
}
_INTERNAL_DESCRIPTIONS = {
    "geography": "States and counties for {year}: state abbreviation, state code, county code "
                 "and county name, one row per county. Used by the tools to find places.",
    "land_area_and_location": "Land area, water area and coordinates of each Census block group "
                              "for {year}. Used by the tools only.",
}


class BuildError(Exception):
    """Raised by `build_all` when any build check fails."""


# ---------------------------------------------------------------- v1 catalog

@cache
def _load_table(path: Path) -> dict:
    return yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.CSafeLoader)


def load_v1_tables(settings: Settings, year: int) -> dict[str, dict]:
    """The year's v1 tables the build reads or checks: every included demographic table (all
    `{year}_CBG_[BC]..` except B99), the Census-count table and the two internal lookups."""
    tables_dir = Path(settings.v1_tables_dir)
    paths = [p for p in sorted(tables_dir.glob(f"{year}_*.yaml"))
             if (_INCLUDED.fullmatch(p.stem) and not p.stem.endswith("_CBG_B99"))
             or p.stem in (f"{year}_METADATA_CBG_FIPS_CODES",
                           f"{year}_METADATA_CBG_GEOGRAPHIC_DATA")]
    tables = {}
    for path in paths:
        table = _load_table(path)
        tables[table["metadata"]["physical_table"]] = table
    return tables


def physical_columns(v1_table: dict) -> list[str]:
    """Every physical column of a v1 table: keys, estimates, margins and unresolved columns."""
    columns = [f["metadata"]["source_column"] for f in v1_table["dimensions"].values()]
    for field in v1_table["measures"].values():
        columns.append(field["metadata"]["source_column"])
        moe = (field.get("margin_of_error") or {}).get("metadata", {}).get("source_column")
        if moe:
            columns.append(moe)
    columns += list(v1_table.get("unresolved_columns") or [])
    meta = v1_table["metadata"]
    if len(columns) != len(set(columns)) or len(columns) != meta["physical_column_count"]:
        raise ValueError(f"{meta['physical_table']}: found {len(set(columns))} columns, "
                         f"expected {meta['physical_column_count']}")
    return columns


def _group_of(field: dict) -> str:
    source = field["metadata"]["source_metadata"] or {}
    return source.get("TABLE_NUMBER") or source["COLUMN_ID"][:4]


def _group_titles(v1_table: dict) -> dict[str, str]:
    """Title of every group version in the table (redistricting topics for P groups)."""
    titles: dict[str, str] = {}
    for field in v1_table["measures"].values():
        source = field["metadata"]["source_metadata"] or {}
        titles.setdefault(_group_of(field), source.get("TABLE_TITLE") or source["COLUMN_TOPIC"])
    for column in v1_table.get("unresolved_columns") or []:
        if _E_ESTIMATE.fullmatch(column):
            titles.setdefault(column[:6] + "E", "")
    return titles


def _assignment_key(group: str) -> str:
    """Race versions A-I belong to their base group."""
    match = _RACE_VERSION.fullmatch(group)
    return match[1] if match else group


def table_groups(v1_table: dict) -> set[str]:
    """Groups of a table at assignment level (race versions folded into their base)."""
    return {_assignment_key(g) for g in _group_titles(v1_table)}


# ---------------------------------------------------------------- generation

def file_name(topic: TopicSpec, year: int) -> str:
    return topic.name if _YEAR_SUFFIX.search(topic.name) else f"{topic.name}_{year}"


def applies(topic: TopicSpec, year: int) -> bool:
    match = _YEAR_SUFFIX.search(topic.name)
    return match is None or int(match[1]) == year


def _is_census_count(topic: TopicSpec) -> bool:
    return topic.table == "REDISTRICTING_CBG_DATA"


def _sentence(title: str) -> str:
    text = _NOISE.sub("", re.sub(r"\s+", " ", title).strip().lower())
    for word in _PROPER:
        text = re.sub(rf"\b{re.escape(word.lower())}\b", word, text)
    return re.sub(r"\(((?:[a-z]{2}, )+[a-z]{2})\)", lambda m: f"({m[1].upper()})", text)


def _summary(topic: TopicSpec, v1_table: dict, present: list[str]) -> str:
    titles = _group_titles(v1_table)
    parts: list[str] = []
    race_versions = False
    for group in present:
        title = titles.get(group)
        if not title:  # base group published only by race (B28009): use a race version
            versions = sorted(g for g in titles if _assignment_key(g) == group and titles[g])
            title = re.sub(r"\s*\([^()]*\)$", "", titles[versions[0]])
        race_versions |= any(_assignment_key(g) == group and g != group for g in titles)
        text = _sentence(title)
        if text not in parts:
            parts.append(text)
    summary = "; ".join(parts) + "."
    return summary + (RACE_NOTE if race_versions else "")


def _description(topic: TopicSpec, year: int, summary: str) -> str:
    if _is_census_count(topic):
        return COUNT_PERIOD + summary + COUNT_CLOSING
    return ACS_PERIOD.format(start=year - 4, year=year) + summary


def _sql_table(settings: Settings, physical: str) -> str:
    return f'"{settings.snowflake_database}"."{settings.snowflake_schema}"."{physical}"'


def _with_file(metadata: list[MemberMetadata], name: str) -> list[MemberMetadata]:
    return [m.model_copy(update={"file": name}) for m in metadata]


def _other_year_has(settings: Settings, topic: TopicSpec, year: int, group: str) -> bool:
    for other in YEARS:
        path = Path(settings.v1_tables_dir) / f"{other}_{topic.table}.yaml"
        if other != year and path.is_file() and group in table_groups(_load_table(path)):
            return True
    return False


def _topic_file(settings, topic, year, v1_table, labels, templates, tables, topics):
    name = file_name(topic, year)
    physical = v1_table["metadata"]["physical_table"]
    available = table_groups(v1_table)
    present = []
    for group in topic.groups:
        if group in available:
            present.append(group)
        elif not _other_year_has(settings, topic, year, group):
            raise ValueError(f"Topic {topic.name}: group {group} is in neither the 2019 nor "
                             f"the 2020 {topic.table} table")
    key, key_meta = key_dimension(v1_table, year)
    dimensions: list[Dimension] = [key]
    measures: list[Measure] = []
    metadata: list[MemberMetadata] = [key_meta]
    for group in present:
        dims, ms, meta = convert_group(v1_table, group, year, labels)
        dimensions += dims
        measures += ms
        metadata += meta
    joins: dict[str, Join] = {}
    for template in templates:
        if template.topic != topic.name:
            continue
        measure, measure_joins, meta = render(template, year, tables, topics=topics)
        measures.append(measure)
        metadata.append(meta)
        for join in measure_joins:
            joins.setdefault(join.name, join)
    title = topic.title if _is_census_count(topic) else f"{topic.title} ({year})"
    file = TopicFile(name=name, title=title,
                     description=_description(topic, year, _summary(topic, v1_table, present)),
                     sql_table=_sql_table(settings, physical), dimensions=dimensions,
                     measures=measures, joins=list(joins.values()))
    return file, _with_file(metadata, name)


def _internal_file(settings, topic, year, v1_table):
    name = file_name(topic, year)
    physical = v1_table["metadata"]["physical_table"]
    dimensions: list[Dimension] = []
    metadata: list[MemberMetadata] = []
    if _KEY in physical_columns(v1_table):
        key, key_meta = key_dimension(v1_table, year)
        dimensions.append(key)
        metadata.append(key_meta)
    for member, column, title, description, kind in _INTERNAL_MEMBERS[topic.name]:
        dimensions.append(Dimension(name=member, title=title, description=description,
                                    sql=f'"{physical}"."{column}"', type=kind))
        metadata.append(MemberMetadata(
            file=name, member=member, kind="dimension", table=physical, source_column=column,
            aggregation="none", definition_source="project"))
    file = TopicFile(name=name, title=f"{topic.title} ({year})",
                     description=_INTERNAL_DESCRIPTIONS[topic.name].format(year=year),
                     sql_table=_sql_table(settings, physical), dimensions=dimensions,
                     measures=[])
    return file, _with_file(metadata, name)


def _referenced_tables(file: TopicFile, metadata: list[MemberMetadata]) -> set[str]:
    tables = {m.table for m in metadata}
    for join in file.joins:
        tables |= {table for table, _ in _REF.findall(join.sql)}
    for meta in metadata:
        tables |= {c.split(".")[0] for c in [*meta.numerator_columns, *meta.denominator_columns]}
    return tables


def generate(settings: Settings, year: int, topics: list[TopicSpec] | None = None
             ) -> tuple[list[TopicFile], BuilderMetadata]:
    """All topic files of one year (internal ones included) and the year's builder metadata."""
    source = Path(settings.source_dir)
    topics = list(topics) if topics is not None else load_topics(source / "topics.yaml")
    v1 = load_v1_tables(settings, year)
    labels = load_census_api_labels(source / "census_api", year)
    tables = column_tables(year, Path(settings.v1_tables_dir))
    templates = expand(load_templates(source / "measures.yaml"), RACE_GROUPS)
    names = {t.name for t in topics}
    unknown = sorted({t.topic for t in templates} - names)
    if unknown:
        raise ValueError(f"Hand-written measures name unknown topics: {', '.join(unknown)}")

    files: list[TopicFile] = []
    members: list[MemberMetadata] = []
    read: set[str] = set()
    for topic in topics:
        if not applies(topic, year):
            continue
        physical = f"{year}_{topic.table}"
        if physical not in v1:
            raise ValueError(f"Topic {topic.name}: no v1 table {physical}")
        if topic.internal:
            file, metadata = _internal_file(settings, topic, year, v1[physical])
        else:
            file, metadata = _topic_file(settings, topic, year, v1[physical], labels,
                                         templates, tables, topics)
        files.append(file)
        members += metadata
        read |= _referenced_tables(file, metadata)
    metadata = BuilderMetadata(
        year=year, members=members,
        table_columns={t: physical_columns(v1[t]) for t in sorted(read)},
        fips_table=f"{year}_METADATA_CBG_FIPS_CODES")
    return files, metadata


def allowed_tables(metadata: Mapping[int, BuilderMetadata]) -> dict[int, list[str]]:
    """Per year, the physical tables read by any topic (internal ones included)."""
    return {year: sorted(meta.table_columns) for year, meta in sorted(metadata.items())}


# ---------------------------------------------------------------- output

def topic_file_dict(file: TopicFile) -> dict:
    return file.model_dump(mode="json", exclude_defaults=True)


def dump_topic_file(file: TopicFile) -> str:
    return yaml.safe_dump(topic_file_dict(file), sort_keys=False, allow_unicode=True,
                          width=100)


def _base_name(file: TopicFile, year: int) -> str:
    return file.name.removesuffix(f"_{year}")


def write_outputs(files: Mapping[int, list[TopicFile]], metadata: Mapping[int, BuilderMetadata],
                  allowed: Mapping[int, list[str]], out_dir: Path,
                  internal: Iterable[str] = ()) -> None:
    """Write `{out}/{year}/<file>.yaml` (internal topics under `{out}/{year}/internal/`),
    `{out}/{year}/builder_metadata.json` and `{out}/allowed_tables.json`. Stale topic files in
    those directories are removed first (generated files are never edited by hand)."""
    out_dir = Path(out_dir)
    internal = set(internal)
    for year, year_files in sorted(files.items()):
        year_dir = out_dir / str(year)
        internal_dir = year_dir / "internal"
        for directory in (year_dir, internal_dir):
            directory.mkdir(parents=True, exist_ok=True)
            for stale in directory.glob("*.yaml"):
                stale.unlink()
        for file in year_files:
            target = internal_dir if _base_name(file, year) in internal else year_dir
            (target / f"{file.name}.yaml").write_text(dump_topic_file(file), encoding="utf-8")
        (year_dir / "builder_metadata.json").write_text(
            metadata[year].model_dump_json(indent=1) + "\n", encoding="utf-8")
    (out_dir / "allowed_tables.json").write_text(
        json.dumps({str(y): t for y, t in sorted(allowed.items())}, indent=1) + "\n",
        encoding="utf-8")


# ---------------------------------------------------------------- checks

@cache
def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _schema_errors(instance: dict, schema_name: str, label: str) -> list[str]:
    validator = jsonschema.Draft202012Validator(_schema(schema_name))
    return [f"{label}: schema {schema_name}: {e.message} at /{'/'.join(map(str, e.path))}"
            for e in validator.iter_errors(instance)]


def _own_table(file: TopicFile) -> str:
    refs = re.findall(r'"([^"]+)"', file.sql_table)
    return refs[-1] if refs else ""


def _check_groups(topics, v1_tables, year) -> list[str]:
    errors = []
    assigned: dict[tuple[str, str], list[str]] = defaultdict(list)
    for topic in topics:
        if applies(topic, year):
            for group in topic.groups:
                assigned[(f"{year}_{topic.table}", group)].append(topic.name)
    for physical, table in sorted(v1_tables.items()):
        if not _INCLUDED.fullmatch(physical) or physical.endswith("_CBG_B99"):
            continue
        for group in sorted(table_groups(table)):
            owners = assigned.get((physical, group), [])
            if not owners:
                errors.append(f"{year}: group {group} of {physical} is not assigned to any "
                              "topic")
            elif len(owners) > 1:
                errors.append(f"{year}: group {group} of {physical} is assigned to more than "
                              f"one topic ({', '.join(owners)})")
    return errors


def _check_file(file, members, own_expected, metadata) -> list[str]:
    errors = []
    name = file.name
    own = _own_table(file)
    if own != own_expected:
        errors.append(f"{name}: reads {own}, expected {own_expected}")
    join_tables: set[str] = set()
    for join in file.joins:
        refs = _REF.findall(join.sql)
        expected = f'"{own}"."{_KEY}" = "{refs[-1][0]}"."{_KEY}"' if refs else None
        if join.relationship != "one_to_one" or join.sql != expected:
            errors.append(f"{name}: join {join.name} is not a one-to-one block-group join")
        join_tables |= {t for t, _ in refs} - {own}

    llm_members = [*file.dimensions, *file.measures]
    counts = defaultdict(int)
    for member in llm_members:
        counts[member.name] += 1
    errors += [f"{name}: member {m} appears more than once" for m, c in counts.items() if c > 1]
    meta_names = {m.member for m in members}
    errors += [f"{name}: member {m} has no builder metadata"
               for m in counts if m not in meta_names]
    errors += [f"{name}: builder metadata member {m} is not in the file"
               for m in sorted(meta_names - set(counts))]

    by_member = {m.member: m for m in members}
    for member in llm_members:
        meta = by_member.get(member.name)
        formula = meta is not None and meta.aggregation == "formula"
        texts = [member.sql, *member.meta.values()]
        for table, column in (ref for text in texts for ref in _REF.findall(text)):
            if table != own and not (formula and table in join_tables):
                errors.append(f"{name}: member {member.name} reads {table} without a declared "
                              "join")
            if column not in set(metadata.table_columns.get(table, ())):
                errors.append(f"{name}: member {member.name}: column {table}.{column} does "
                              "not exist")
        if meta is not None and meta.table != own:
            errors.append(f"{name}: member {member.name} metadata table {meta.table} is not "
                          f"{own}")

    for meta in members:
        if meta.definition_source != "hand_written":
            continue
        for field in ("coverage_sql", "normalized_sql", "subject_definitions_page"):
            if not getattr(meta, field):
                errors.append(f"{name}: hand-written measure {meta.member} has no {field}")
        if not meta.numerator_columns or not meta.denominator_columns:
            errors.append(f"{name}: hand-written measure {meta.member} has no input columns")
        for qualified in [*meta.numerator_columns, *meta.denominator_columns]:
            table, _, column = qualified.partition(".")
            if column not in set(metadata.table_columns.get(table, ())):
                errors.append(f"{name}: input column {qualified} of {meta.member} does not "
                              "exist")
            if table != own and table not in join_tables:
                errors.append(f"{name}: hand-written measure {meta.member} reads {table} "
                              "without a declared join")

    text = dump_topic_file(file)
    errors += [f"{name}: LLM-facing file contains {word}"
               for word in FORBIDDEN_IN_LLM_FILES if word in text]
    errors += _schema_errors(topic_file_dict(file), "topic_file.schema.json", name)
    return errors


def _check_e_columns(files, v1_tables, year) -> list[str]:
    """The 148 undocumented E columns per year (estimates and margins): every estimate is a
    described member and every margin is the `moe_sql` of a described member."""
    described: dict[str, str] = {}
    for member in (m for f in files for m in [*f.dimensions, *f.measures]):
        for sql in (member.sql, member.meta.get("moe_sql")):
            if sql:
                described[sql] = member.description
    found = []
    for physical, table in sorted(v1_tables.items()):
        if _INCLUDED.fullmatch(physical) and not physical.endswith("_CBG_B99"):
            found += [(physical, c) for c in table.get("unresolved_columns") or []
                      if _E_COLUMN.fullmatch(c)]
    errors = [f"{year}: E column {t}.{c} has no description"
              for t, c in found if not described.get(f'"{t}"."{c}"')]
    if len(found) != E_COLUMNS_PER_YEAR:
        errors.append(f"{year}: found {len(found)} E columns, expected {E_COLUMNS_PER_YEAR}")
    return errors


def _check_tables(metadata: BuilderMetadata) -> list[str]:
    year = metadata.year
    errors = []
    for table in metadata.table_columns:
        if "GEOMETRY" in table or table in {f"{year}_{t}" for t in EXCLUDED_TABLES}:
            errors.append(f"{year}: excluded table {table} is in the allowed tables")
        elif not table.startswith(f"{year}_"):
            errors.append(f"{year}: table {table} is not a {year} table")
    if metadata.fips_table not in metadata.table_columns:
        errors.append(f"{year}: county lookup {metadata.fips_table} is not an allowed table")
    return errors


def run_checks(files: list[TopicFile], metadata: BuilderMetadata, topics: list[TopicSpec],
               v1_tables: Mapping[str, dict], settings: Settings) -> list[str]:
    """Build checks of spec section 5 for one year; returns the errors (empty = pass).

    Topic size is not checked here: it is only a warning (`size_warnings`).
    """
    year = metadata.year
    errors = _check_groups(topics, v1_tables, year)
    expected = {file_name(t, year): t for t in topics if applies(t, year)}
    names = [f.name for f in files]
    errors += [f"{year}: topic file {n} is missing" for n in expected if n not in names]
    errors += [f"{year}: file {n} has no topic" for n in names if n not in expected]
    errors += [f"{year}: file {n} appears more than once" for n in set(names)
               if names.count(n) > 1]

    by_file: dict[str, list[MemberMetadata]] = defaultdict(list)
    for meta in metadata.members:
        by_file[meta.file].append(meta)
    errors += [f"{year}: builder metadata names unknown file {n}" for n in by_file
               if n not in names]
    for file in files:
        topic = expected.get(file.name)
        own = f"{year}_{topic.table}" if topic else ""
        errors += _check_file(file, by_file.get(file.name, []), own, metadata)

    errors += _check_e_columns(files, v1_tables, year)
    errors += _check_tables(metadata)
    errors += _schema_errors(metadata.model_dump(mode="json", warnings=False),
                             "builder_metadata.schema.json", f"{year} builder metadata")
    return errors


def estimated_tokens(file: TopicFile) -> int:
    """Rough token estimate of the file as `get_schema` returns it: characters / 4."""
    return -(-len(dump_topic_file(file)) // 4)


def size_warnings(files: list[TopicFile], settings: Settings) -> list[str]:
    """Topic files over `settings.topic_token_limit` (a warning, never an error)."""
    limit = settings.topic_token_limit
    return [f"{f.name}: about {tokens:,} tokens (limit {limit:,}); reviewed topics are not "
            "split" for f in files if (tokens := estimated_tokens(f)) > limit]


def build_all(settings: Settings, out_dir: Path | None = None) -> list[str]:
    """Generate, check and write both years; returns the size warnings.

    Raises BuildError (and writes nothing) if any check fails.
    """
    topics = load_topics(Path(settings.source_dir) / "topics.yaml")
    files, metadata, errors, warnings = {}, {}, [], []
    for year in YEARS:
        files[year], metadata[year] = generate(settings, year, topics)
        errors += run_checks(files[year], metadata[year], topics,
                             load_v1_tables(settings, year), settings)
        warnings += size_warnings(files[year], settings)
    if errors:
        raise BuildError("\n".join(errors))
    write_outputs(files, metadata, allowed_tables(metadata),
                  Path(out_dir) if out_dir is not None else Path(settings.semantic_dir),
                  internal={t.name for t in topics if t.internal})
    return warnings
