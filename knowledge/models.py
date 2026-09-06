import uuid

from django.db import models
from pgvector.django import VectorField


class KnowledgeChunk(models.Model):
    """One retrievable unit of knowledge — an FAQ answer, policy paragraph, or procedure."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=200)
    content = models.TextField(help_text='The actual text the bot can use')
    category = models.CharField(max_length=100, blank=True, help_text='Optional grouping, e.g. "shipping", "returns"')
    embedding = VectorField(dimensions=384, null=True, blank=True,
                            help_text='Regenerated automatically whenever content changes')
    is_active = models.BooleanField(default=True, help_text='Inactive chunks are excluded from retrieval')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_active', 'category']),
        ]

    def __str__(self):
        return self.title


class Rule(models.Model):
    """Deterministic keyword → response override, checked before the LLM runs."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text='Admin label')
    trigger_keywords = models.JSONField(help_text='List of strings; case-insensitive substring match for v1')
    response_text = models.TextField(help_text='What to send when triggered')
    short_circuits_llm = models.BooleanField(
        default=True,
        help_text='If True, sends response_text directly and skips the LLM call; '
                  'if False, folds the rule into the LLM context instead',
    )
    priority = models.IntegerField(default=100, help_text='Lower runs first; first match wins')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority']

    def __str__(self):
        return self.name


class BotSettings(models.Model):
    """Singleton holding global bot behavior not tied to a specific provider."""

    fallback_message = models.TextField(
        help_text='Sent when the bot has low/no confidence, '
                  'e.g. "I\'m not sure about that — let me get a team member to help."'
    )
    max_context_messages = models.PositiveIntegerField(
        default=10, help_text='How many recent messages count as short-term memory')
    memory_summary_trigger_count = models.PositiveIntegerField(
        default=20, help_text='Regenerate Customer.memory_summary every N new messages')
    business_hours = models.JSONField(blank=True, default=dict, null=True,
                                      help_text='Optional, used by rules/prompt context')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={"fallback_message": "I'm not sure about that — let me get a team member to help."},
        )
        return obj

    def __str__(self):
        return 'Bot Settings'
