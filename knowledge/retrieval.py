"""RAG retrieval and deterministic rule matching."""
from pgvector.django import CosineDistance

from .embeddings import embed_text
from .models import KnowledgeChunk, Rule


def retrieve_relevant_chunks(query_text: str, top_k: int = 3):
    """Top-k active chunks by cosine similarity. Returns [] if the query can't
    be embedded (e.g. model unavailable) or no chunks have embeddings yet."""
    vector = embed_text(query_text)
    if vector is None:
        return KnowledgeChunk.objects.none()
    return (
        KnowledgeChunk.objects.filter(is_active=True, embedding__isnull=False)
        .annotate(distance=CosineDistance("embedding", vector))
        .order_by("distance")[:top_k]
    )


def match_rule(message_text: str) -> "Rule | None":
    """First active rule (by priority) whose keyword appears in the text (case-insensitive)."""
    lowered = message_text.lower()
    for rule in Rule.objects.filter(is_active=True).order_by("priority", "created_at"):
        keywords = rule.trigger_keywords or []
        if isinstance(keywords, str):
            keywords = [keywords]
        if any(str(kw).lower() in lowered for kw in keywords if kw):
            return rule
    return None
