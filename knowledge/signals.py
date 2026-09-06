from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import KnowledgeChunk
from .tasks import generate_embedding


@receiver(post_save, sender=KnowledgeChunk)
def enqueue_embedding_on_save(sender, instance, **kwargs):
    # Enqueue rather than embedding inline, so admin saves stay fast.
    generate_embedding.delay(instance.pk)
