"""RAG retrieval and deterministic rule matching."""
import re

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
    """First active rule (by priority) whose keyword appears in the text as a
    whole word or phrase, case-insensitive.

    Word-boundary matching: raw substring matching made short greeting
    keywords like "hi" fire inside unrelated words — e.g. a visitor writing
    "1 chinlder 1 year old" (typo for children) triggered a Welcome rule in
    the middle of sharing travel details. (?<!\\w)…(?!\\w) keeps multi-word
    keywords ("money back") working and is unicode-aware, so non-Latin
    keywords still match without partial-word false positives."""
    lowered = (message_text or "").lower()
    for rule in Rule.objects.filter(is_active=True).order_by("priority", "created_at"):
        keywords = rule.trigger_keywords or []
        if isinstance(keywords, str):
            keywords = [keywords]
        for kw in keywords:
            kw = str(kw).strip().lower()
            if not kw:
                continue
            if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", lowered):
                return rule
    return None

