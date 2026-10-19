"""Embedding generation with an explicitly named sentence-transformers model.

The same model must encode catalog documents (at index build) and queries (at
search time). Models are cached per name; there is no silent default here.
"""

from functools import lru_cache


@lru_cache(maxsize=4)
def _load(model_name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def embed(texts: list[str], model_name: str) -> list[list[float]]:
    """Return normalized embedding vectors, one per input text."""
    if not texts:
        return []
    vectors = _load(model_name).encode(texts, normalize_embeddings=True)
    return [[float(x) for x in v] for v in vectors]
