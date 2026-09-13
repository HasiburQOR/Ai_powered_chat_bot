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


PROFILE_INTRO_MESSAGE_DEFAULT = (
    "May I have the following information?\n"
    "- Name and WhatsApp number?\n"
    "- Nationality and country of residence? If you have a GCC residence card, its expiry date too.\n"
    "- Approximate travel date?\n"
    "- How many days package are you looking for?\n"
    "- How many people are travelling together? For children, please share their ages."
)

# Canonical last-resort reply, used when the LLM could not answer even after
# retries. Deliberately NEVER worded like the old "I'm not sure about that —
# let me get a team member to help.": that reads as the bot being oblivious,
# and a travel lead who hears "I don't know" simply leaves. The default keeps
# the conversation moving and asks for trip details so the exchange stays
# salvageable for a human follow-up.
FALLBACK_MESSAGE_DEFAULT = (
    "Sorry, I couldn't process that just now — please send your message again. "
    "Meanwhile, tell me your destination, travel dates and number of travellers, "
    "and I'll get the details ready for you."
)

# Cache key under which engine._settings() keeps this singleton (~30s TTL);
# save() deletes it so dashboard edits apply to the very next message.
BOT_SETTINGS_CACHE_KEY = "bot:settings"


class BotSettings(models.Model):
    """Singleton holding global bot behavior not tied to a specific provider."""

    fallback_message = models.TextField(
        help_text='Sent only when the LLM could not answer even after retries '
                  '(provider outage, timeout...). Never word it as "I\'m not '
                  'sure / let me get a team member" — that reads as the bot '
                  'being oblivious and kills travel leads. Leave the default; '
                  'it invites the visitor to re-send and keeps gathering trip '
                  'details.'
    )
    max_context_messages = models.PositiveIntegerField(
        default=10, help_text='How many recent messages count as short-term memory')
    memory_summary_trigger_count = models.PositiveIntegerField(
        default=20, help_text='Regenerate Customer.memory_summary every N new messages')
    business_hours = models.JSONField(blank=True, default=dict, null=True,
                                      help_text='Optional, used by rules/prompt context')
    profile_collection_enabled = models.BooleanField(
        default=True,
        help_text='When on, the bot asks for a short set of travel-profile details '
                  'once, the first time a visitor shows travel interest, and files '
                  'their answers into a customer profile.')
    profile_intro_message = models.TextField(
        default=PROFILE_INTRO_MESSAGE_DEFAULT,
        blank=True,
        help_text='Scripted question list, sent as a second bubble the first time a '
                  'visitor shows travel intent (requires profile collection to be '
                  'enabled; leave blank to rely on the AI weaving questions in).')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.pk = 1  # Enforce singleton
        super().save(*args, **kwargs)
        # engine._settings() caches this row for ~30s; a dashboard save must
        # apply to the very next visitor message, not 30s later.
        try:
            from django.core.cache import cache
            cache.delete(BOT_SETTINGS_CACHE_KEY)
        except Exception:
            pass

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={"fallback_message": FALLBACK_MESSAGE_DEFAULT},
        )
        return obj

    def __str__(self):
        return 'Bot Settings'
