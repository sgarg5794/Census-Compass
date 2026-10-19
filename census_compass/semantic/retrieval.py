"""Hybrid (vector + full-text, RRF-reranked) retrieval over the LanceDB index."""

from collections import defaultdict
from pathlib import Path

import lancedb
from lancedb.rerankers import RRFReranker

from census_compass.config import get_settings
from census_compass.contracts import MemberHit, SearchHit
from census_compass.semantic.embeddings import embed

CANDIDATES = 20
MAX_MEMBERS = 5


def _quote(value: str) -> str:
    return value.replace("'", "''")


def _ranked(table, query: str, vector: list[float], where: str) -> list[dict]:
    rows = (
        table.search(query_type="hybrid")
        .vector(vector)
        .text(query)
        .where(where, prefilter=True)
        .rerank(RRFReranker())
        .limit(CANDIDATES)
        .to_list()
    )
    return rows


def search(
    query: str,
    year: int,
    population_group: str | None,
    k: int = 5,
    *,
    index_dir: Path | None = None,
    model_name: str | None = None,
) -> list[SearchHit]:
    """Return up to `k` files for `query`, each with up to 5 matching members.

    Score = file's own RRF score + sum of its members' RRF scores.
    """
    if not query.strip():
        return []
    settings = get_settings()
    model_name = model_name or settings.embedding_model
    db = lancedb.connect(str(index_dir if index_dir is not None else settings.lancedb_path))
    vector = embed([query], model_name)[0]

    file_rows = _ranked(db.open_table("files"), query, vector, f"year = {int(year)}")
    member_where = f"year = {int(year)}"
    if population_group is None:
        member_where += " AND population_group IS NULL"
    else:
        member_where += (
            f" AND (population_group IS NULL OR population_group = '{_quote(population_group)}')"
        )
    member_rows = _ranked(db.open_table("members"), query, vector, member_where)

    scores: dict[str, float] = defaultdict(float)
    for r in file_rows:
        scores[r["file"]] += float(r["_relevance_score"])
    matched: dict[str, list[MemberHit]] = defaultdict(list)
    for r in member_rows:
        scores[r["file"]] += float(r["_relevance_score"])
        if len(matched[r["file"]]) < MAX_MEMBERS:
            matched[r["file"]].append(MemberHit(name=r["member"], title=r["title"]))

    descriptions = _file_info(db, year, set(scores))
    ranked = sorted(scores, key=lambda f: scores[f], reverse=True)[: min(k, 5)]
    return [
        SearchHit(
            name=f,
            title=descriptions[f][0],
            description=descriptions[f][1],
            matched_members=matched.get(f, []),
        )
        for f in ranked
        if f in descriptions
    ]


def _file_info(db, year: int, names: set[str]) -> dict[str, tuple[str, str]]:
    """Title and description per file; description is the file text minus its title."""
    rows = db.open_table("files").search().where(f"year = {int(year)}").limit(100000).to_list()
    out = {}
    for r in rows:
        if r["file"] in names:
            text = r["text"]
            desc = text[len(r["title"]) :].strip() if text.startswith(r["title"]) else text
            out[r["file"]] = (r["title"], desc)
    return out
