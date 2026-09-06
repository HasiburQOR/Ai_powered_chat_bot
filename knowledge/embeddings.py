"""Local embedding generation via sentence-transformers.

The model is loaded lazily and cached at module level so the Celery worker
loads it once instead of on every chunk save.
"""
from __future__ import annotations

_MODEL = None
EMBEDDING_DIMENSIONS = 384


def get_model():
    global _MODEL
    if _MODEL is None:
        from sentence_transformers import SentenceTransformer

        _MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _MODEL


def embed_text(text: str) -> list[float] | None:
    """Return a 384-dim vector, or None if the library isn't available."""
    try:
        model = get_model()
    except Exception:
        return None
    return model.encode(text, normalize_embeddings=True).tolist()
