from celery import shared_task


@shared_task
def generate_embedding(chunk_id):
    """Embed a KnowledgeChunk's content and store the vector. Runs in the worker
    so admin edits never block on embedding latency."""
    from .embeddings import embed_text
    from .models import KnowledgeChunk

    chunk = KnowledgeChunk.objects.filter(pk=chunk_id).first()
    if chunk is None or not chunk.is_active:
        return
    vector = embed_text(f"{chunk.title}\n{chunk.content}")
    if vector is not None:
        KnowledgeChunk.objects.filter(pk=chunk_id).update(embedding=vector)


@shared_task
def regenerate_all_embeddings():
    """Utility task: re-embed every active chunk (e.g. after a model change)."""
    from .models import KnowledgeChunk

    for chunk_id in KnowledgeChunk.objects.filter(is_active=True).values_list("id", flat=True):
        generate_embedding.delay(chunk_id)
