import uuid

from django.core.exceptions import ValidationError
from django.db import models

from core.fields import EncryptedTextField


class LLMConfig(models.Model):
    """One LLM provider setup. Exactly one is active at a time."""

    class Provider(models.TextChoices):
        OPENAI_COMPATIBLE = 'openai_compatible', 'OpenAI-compatible'
        ANTHROPIC = 'anthropic', 'Anthropic'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, help_text='e.g. "Primary — DeepSeek Chat"')
    provider = models.CharField(max_length=30, choices=Provider.choices)
    api_base_url = models.URLField(
        blank=True,
        help_text='Required for openai_compatible (e.g. https://api.deepseek.com/v1); ignored for anthropic',
    )
    api_key = EncryptedTextField()
    model_name = models.CharField(max_length=200, help_text='e.g. deepseek-chat, gpt-4o-mini, claude-sonnet-4-6')
    system_prompt = models.TextField(help_text='Base persona/instructions, prepended to every call')
    temperature = models.FloatField(default=0.7)
    max_tokens = models.IntegerField(default=1024)
    is_active = models.BooleanField(default=False, help_text='Only one config can be active at a time')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if self.is_active:
            # Deactivate all other configs so exactly one is active.
            LLMConfig.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    def clean(self):
        if self.provider == self.Provider.OPENAI_COMPATIBLE and not self.api_base_url:
            raise ValidationError({'api_base_url': 'api_base_url is required for openai_compatible providers.'})

    def __str__(self):
        return self.name
