import uuid

from django.db import models

from core.fields import EncryptedJSONField


class Channel(models.Model):
    """One connection to an external surface (IG account, Messenger page, WordPress site)."""

    class ChannelType(models.TextChoices):
        INSTAGRAM = 'instagram', 'Instagram'
        MESSENGER = 'messenger', 'Messenger'
        WORDPRESS = 'wordpress', 'WordPress'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel_type = models.CharField(max_length=20, choices=ChannelType.choices)
    name = models.CharField(max_length=200, help_text='Admin-facing label, e.g. "Main IG Account"')
    credentials = EncryptedJSONField(blank=True, default=dict)
    is_active = models.BooleanField(default=True, help_text='Whether the bot responds on this channel')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'channel'
        verbose_name_plural = 'channels'

    def __str__(self):
        return f'{self.name} ({self.get_channel_type_display()})'
