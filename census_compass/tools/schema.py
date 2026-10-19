"""Agent tools: search_schema and get_schema."""

from pathlib import Path

from census_compass.config import get_settings
from census_compass.contracts import TopicFile, load_topic_files
from census_compass.semantic.retrieval import search

YEARS = (2019, 2020)


def _search_schema(
    query: str,
    year: int,
    population_group: str | None = None,
    *,
    index_dir: Path | None = None,
    model_name: str | None = None,
) -> dict:
    settings = get_settings()
    hits = search(
        query,
        year,
        population_group,
        index_dir=index_dir if index_dir is not None else settings.lancedb_path,
        model_name=model_name or settings.embedding_model,
    )
    return {"results": [h.model_dump() for h in hits]}


def _get_schema(names: list[str], *, semantic_dir: Path | None = None) -> dict:
    sdir = semantic_dir if semantic_dir is not None else get_settings().semantic_dir
    available: dict[str, TopicFile] = {}
    for year in YEARS:
        if (Path(sdir) / str(year)).is_dir():
            for topic in load_topic_files(sdir, year):
                available[topic.name] = topic
    files, errors = [], []
    for name in names:
        if name in available:
            files.append(available[name].model_dump())
        else:
            errors.append({"name": name, "valid_names": sorted(available)})
    return {"files": files, "errors": errors}


def search_schema(query: str, year: int, population_group: str | None = None) -> dict:
    """Find Census topic files and fields relevant to a question.

    Call this first to discover which schema files (and which measures or
    dimensions in them) can answer the user's question. Search by concept in
    plain words, e.g. "median household income" or "unemployment rate".

    Args:
        query: Natural-language description of the data needed.
        year: Data year, 2019 or 2020. Only files for that year are searched.
        population_group: Optional race/ethnicity group code (for example "D"
            for Asian alone). When given, race-specific fields for other groups
            are excluded; general (all-population) fields are always kept.

    Returns:
        {"results": [{"name", "title", "description", "matched_members":
        [{"name", "title"}]}]} with up to 5 files, best first. Pass the file
        names to get_schema to see full field definitions. An empty list means
        nothing relevant was found.
    """
    return _search_schema(query, year, population_group)


def get_schema(names: list[str]) -> dict:
    """Fetch the full definition of one or more schema files by name.

    Use after search_schema. Returns every dimension and measure with its
    title, description, SQL expression, type, and format, plus the table and
    joins, which you need to write a correct query.

    Args:
        names: Exact file names as returned by search_schema (they include the
            year, e.g. "poverty_2020").

    Returns:
        {"files": [full file definitions], "errors": [{"name", "valid_names"}]}.
        Unknown names appear in errors together with all valid names.
    """
    return _get_schema(names)
