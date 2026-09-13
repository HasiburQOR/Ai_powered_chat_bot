"""Celery worker boot hooks.

worker_process_init fires once per worker PROCESS (with --pool=threads that is
once per worker, shared by the whole thread pool), so pre-warming the embedding
model here loads MiniLM exactly once per worker instead of lazily on the first
visitor message — which used to add a multi-second stall to the first reply
after every worker start/restart.
"""
import logging

from celery.signals import worker_process_init

logger = logging.getLogger(__name__)


@worker_process_init.connect
def prewarm_embedding_model(**_kwargs):
    try:
        from knowledge.embeddings import get_model

        get_model()
        logger.info("Embedding model pre-warmed for the Celery worker pool")
    except Exception:
        # Never block worker boot over this; RAG lookups already degrade
        # gracefully (empty chunks) when the model is unavailable.
        logger.warning("Embedding model pre-warm failed", exc_info=True)
