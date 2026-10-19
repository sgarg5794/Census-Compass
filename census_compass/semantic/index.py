"""Build the LanceDB search index (tables `files` and `members`) from topic files."""

import shutil
from pathlib import Path

import lancedb

from census_compass.contracts import load_topic_files
from census_compass.semantic.embeddings import embed

YEARS = (2019, 2020)


def _rows(semantic_dir: Path) -> tuple[list[dict], list[dict]]:
    files: list[dict] = []
    members: list[dict] = []
    for year in YEARS:
        if not (Path(semantic_dir) / str(year)).is_dir():
            continue
        for topic in load_topic_files(semantic_dir, year):
            files.append(
                {
                    "year": year,
                    "file": topic.name,
                    "member": "",
                    "title": topic.title,
                    "text": f"{topic.title} {topic.description}",
                    "population_group": None,
                }
            )
            for m in [*topic.dimensions, *topic.measures]:
                members.append(
                    {
                        "year": year,
                        "file": topic.name,
                        "member": m.name,
                        "title": m.title,
                        "text": f"{m.title} {m.description}",
                        "population_group": m.meta.get("population_group"),
                    }
                )
    return files, members


def _write_table(db, name: str, rows: list[dict], model_name: str) -> None:
    if not rows:
        raise ValueError(f"No rows to index for table {name!r}")
    vectors = embed([r["text"] for r in rows], model_name)
    data = [{**r, "vector": v} for r, v in zip(rows, vectors, strict=True)]
    table = db.create_table(name, data=data, mode="overwrite")
    table.create_fts_index("text", use_tantivy=False, replace=True)


def build_index(semantic_dir: Path, index_dir: Path, model_name: str) -> None:
    """Build both tables in a sibling temp dir, then swap it over `index_dir`.

    If anything fails, the existing index is left untouched.
    """
    index_dir = Path(index_dir)
    tmp = index_dir.with_name(index_dir.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    try:
        files, members = _rows(Path(semantic_dir))
        tmp.parent.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(tmp))
        _write_table(db, "files", files, model_name)
        _write_table(db, "members", members, model_name)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    if index_dir.exists():
        shutil.rmtree(index_dir)
    tmp.rename(index_dir)
