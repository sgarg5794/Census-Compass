"""Shared data contracts and file loaders used across all work streams."""

import json
from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Dimension(_Model):
    name: str
    title: str
    description: str
    sql: str
    type: Literal["string", "number"]
    primary_key: bool = False
    format: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class Measure(_Model):
    name: str
    title: str
    description: str
    sql: str
    type: Literal["sum", "number"]
    format: str
    meta: dict[str, str] = Field(default_factory=dict)


class Join(_Model):
    name: str
    relationship: Literal["one_to_one", "many_to_one"]
    sql: str


class TopicFile(_Model):
    name: str
    title: str
    description: str
    sql_table: str
    dimensions: list[Dimension]
    measures: list[Measure]
    joins: list[Join] = Field(default_factory=list)


class MemberMetadata(_Model):
    file: str
    member: str
    kind: Literal["dimension", "measure"]
    table: str
    source_column: str | None
    aggregation: Literal["sum", "none", "formula"]
    definition_source: Literal["dictionary", "census_api", "hand_written", "project"]
    subject_definitions_page: str | None = None
    normalized_sql: str | None = None
    numerator_columns: list[str] = Field(default_factory=list)
    denominator_columns: list[str] = Field(default_factory=list)
    coverage_sql: str | None = None
    population_group: str | None = None


class BuilderMetadata(_Model):
    year: int
    members: list[MemberMetadata]
    table_columns: dict[str, list[str]]
    fips_table: str


class AllowedTables(_Model):
    tables: dict[int, list[str]]


class Period(_Model):
    year: Literal[2019, 2020]
    label: str
    sources: list[Literal["acs", "census_count"]]


class YearResolution(_Model):
    status: Literal["ok", "ambiguous", "unavailable"]
    periods: list[Period] = Field(default_factory=list)
    comparison: bool = False
    message: str = ""


class GeographyResolution(_Model):
    status: Literal["ok", "ambiguous", "unsupported", "not_found", "no_data"]
    year: int
    name: str | None = None
    level: Literal["nation", "state", "county", "block_group"] | None = None
    sql_filter: str | None = None
    block_groups: int | None = None
    candidates: list[str] = Field(default_factory=list)
    message: str = ""


class PopulationGroupResolution(_Model):
    status: Literal["ok", "ambiguous", "not_found"]
    code: str | None = None
    label: str | None = None
    candidates: list[str] = Field(default_factory=list)
    message: str = ""


class MemberHit(_Model):
    name: str
    title: str


class SearchHit(_Model):
    name: str
    title: str
    description: str
    matched_members: list[MemberHit]


class Completeness(_Model):
    row: int
    column: str
    block_groups: int
    missing_inputs: dict[str, int] = Field(default_factory=dict)
    unknown_denominator: int | None = None
    coverage: float | None = None
    missing_moe: int | None = None


class Outcome(StrEnum):
    ANSWERED = "answered"
    CLARIFICATION_NEEDED = "clarification_needed"
    NOT_AVAILABLE = "not_available"
    UNSUPPORTED = "unsupported"
    QUERY_REJECTED = "query_rejected"
    TIMED_OUT = "timed_out"
    DATABASE_FAILURE = "database_failure"
    MODEL_FAILURE = "model_failure"
    TRUNCATED = "truncated"


class QueryResult(_Model):
    outcome: Outcome
    rows: list[dict] = Field(default_factory=list)
    completeness: list[Completeness] = Field(default_factory=list)
    truncated: bool = False
    message: str = ""
    query_id: str | None = None


class QueryRejected(Exception):
    def __init__(self, reason: str, suggestions: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.suggestions = suggestions if suggestions is not None else []


class CountyRecord(_Model):
    state_abbr: str
    state_fips: str
    county_fips: str
    county: str
    block_groups: int


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_text(encoding="utf-8")


def load_builder_metadata(semantic_dir: Path, year: int) -> BuilderMetadata:
    path = Path(semantic_dir) / str(year) / "builder_metadata.json"
    return BuilderMetadata.model_validate_json(_read_text(path))


def load_allowed_tables(semantic_dir: Path) -> AllowedTables:
    raw = json.loads(_read_text(Path(semantic_dir) / "allowed_tables.json"))
    return AllowedTables(tables={int(year): tables for year, tables in raw.items()})


def load_topic_files(semantic_dir: Path, year: int) -> list[TopicFile]:
    year_dir = Path(semantic_dir) / str(year)
    if not year_dir.is_dir():
        raise FileNotFoundError(f"Directory not found: {year_dir}")
    return [
        TopicFile.model_validate(yaml.safe_load(_read_text(path)))
        for path in sorted(year_dir.glob("*.yaml"), key=lambda p: p.name)
    ]


def load_geography(source_dir: Path, year: int) -> list[CountyRecord]:
    path = Path(source_dir) / "geography" / f"{year}.json"
    return [CountyRecord.model_validate(item) for item in json.loads(_read_text(path))]
