"""Pick the embedding model: recall@5 of each candidate on the development question set.

Usage:
    python scripts/select_embedding_model.py --candidates BAAI/bge-small-en-v1.5 \
        thenlper/gte-small sentence-transformers/all-MiniLM-L6-v2

The held-out set is the acceptance test for the final search; it is never used to
choose a model, so running on it requires --acceptance.
"""

import argparse
import sys
import tempfile
from pathlib import Path

import yaml

K = 5
DEFAULT_SET = "evaluations/search_dev.yaml"
HELDOUT_NAME = "search_heldout.yaml"


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--candidates", nargs="+", required=True, help="embedding model names")
    parser.add_argument("--semantic-dir", default=None, help="directory of topic files")
    parser.add_argument("--set", default=DEFAULT_SET, help="question set (default: dev)")
    parser.add_argument(
        "--acceptance",
        action="store_true",
        help="allow running on the held-out set (final acceptance check only)",
    )
    args = parser.parse_args(argv)
    if Path(args.set).name == HELDOUT_NAME and not args.acceptance:
        parser.error(
            "the held-out set is only for the final acceptance check; "
            "tuning on it is not allowed (pass --acceptance to run it)"
        )
    return args


def compute_recall(questions: list[dict], results: dict[str, list[str]], k: int = K) -> dict:
    """Recall@k = share of questions whose required files are ALL in the top k results."""
    hits = 0
    by_year: dict[int, list[bool]] = {}
    misses = []
    for q in questions:
        got = results.get(q["id"], [])[:k]
        missing = [f for f in q["required_files"] if f not in got]
        ok = not missing
        hits += ok
        by_year.setdefault(q["year"], []).append(ok)
        if not ok:
            misses.append({"id": q["id"], "missing": missing, "got": got})
    return {
        "overall": hits / len(questions) if questions else 0.0,
        "by_year": {y: sum(v) / len(v) for y, v in sorted(by_year.items())},
        "misses": misses,
    }


def run_model(model: str, questions: list[dict], semantic_dir: Path | None) -> dict:
    from census_compass.config import get_settings
    from census_compass.semantic.index import build_index
    from census_compass.semantic.retrieval import search

    sem = Path(semantic_dir) if semantic_dir else get_settings().semantic_dir
    with tempfile.TemporaryDirectory() as tmp:
        index_dir = Path(tmp) / "index"
        build_index(sem, index_dir, model)
        results = {
            q["id"]: [
                h.name
                for h in search(
                    q["question"],
                    q["year"],
                    q["population_group"],
                    k=K,
                    index_dir=index_dir,
                    model_name=model,
                )
            ]
            for q in questions
        }
    return compute_recall(questions, results)


def main(argv=None) -> int:
    args = parse_args(argv)
    questions = yaml.safe_load(Path(args.set).read_text())
    print(f"Question set: {args.set} ({len(questions)} questions), recall@{K}")
    best = None
    for model in args.candidates:
        rep = run_model(model, questions, args.semantic_dir)
        years = ", ".join(f"{y}: {r:.1%}" for y, r in rep["by_year"].items())
        print(f"\n{model}\n  recall@{K}: {rep['overall']:.1%}  ({years})")
        for m in rep["misses"]:
            print(f"  miss {m['id']}: missing {m['missing']}; top {K} was {m['got']}")
        if best is None or rep["overall"] > best[1]:
            best = (model, rep["overall"])
    if best:
        print(f"\nBest: {best[0]} ({best[1]:.1%})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
