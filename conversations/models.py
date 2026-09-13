import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import Agent
from platforms.models import Channel


class Customer(models.Model):
    """One row per unique end-user per channel. Long-term memory lives here."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name='customers')
    external_id = models.CharField(max_length=255, help_text='IGSID, PSID, or widget session/visitor ID')
    display_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True, help_text='Optional; shared by the visitor during chat or entered manually')
    phone = models.CharField(max_length=50, blank=True, help_text='Optional; shared by the visitor during chat or entered manually')
    memory_summary = models.TextField(blank=True, help_text='Regenerated periodically by Celery task')
    memory_summary_at = models.DateTimeField(
        null=True, blank=True,
        help_text='When memory_summary was last regenerated; lets the idle-summarizer '
                  'beat task skip customers whose summary is already current')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def has_contact_details(self) -> bool:
        return bool(self.email or self.phone)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['channel', 'external_id'], name='unique_channel_external_id'),
        ]

    def __str__(self):
        return self.display_name or f'{self.channel} user {self.external_id}'


class Conversation(models.Model):
    class Status(models.TextChoices):
        BOT = 'bot', 'Bot'
        ESCALATED = 'escalated', 'Escalated'
        HUMAN = 'human', 'Human'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name='conversations')
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOT,
                              help_text='Stubbed for future handoff')
    assigned_agent = models.ForeignKey(Agent, on_delete=models.SET_NULL, null=True, blank=True,
                                       related_name='conversations', help_text='Stubbed for future handoff')
    started_at = models.DateTimeField(auto_now_add=True)
    last_message_at = models.DateTimeField(help_text='Updated on every new message; used for sorting/idle detection')

    class Meta:
        ordering = ['-last_message_at']

    def __str__(self):
        return f'Conversation with {self.customer} ({self.status})'


class Message(models.Model):
    class SenderType(models.TextChoices):
        CUSTOMER = 'customer', 'Customer'
        BOT = 'bot', 'Bot'
        AGENT = 'agent', 'Agent'
        SYSTEM = 'system', 'System'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    sender_type = models.CharField(max_length=20, choices=SenderType.choices)
    content = models.TextField()
    raw_payload = models.JSONField(blank=True, null=True,
                                   help_text='Original webhook payload, for debugging (dashboard-only)')
    # No auto_now_add: save() stamps this itself with a per-conversation
    # STRICTLY INCREASING guarantee (see save below).
    created_at = models.DateTimeField(editable=False)

    class Meta:
        indexes = [
            models.Index(fields=['conversation', 'created_at']),
        ]
        ordering = ['created_at']

    def save(self, *args, **kwargs):
        if self._state.adding and self.created_at is None:
            # Wall clocks stall: Windows ticks only every ~1-15ms, so messages
            # written in quick succession used to receive IDENTICAL created_at
            # values. Anything filtering created_at > watermark then lost
            # bubbles forever - the widget poller showed an endless
            # "typing..." because the reply shared the watermark's timestamp
            # to the microsecond. Nudge each new message 1us past the
            # conversation's latest instead of trusting the clock.
            latest = Message.objects.filter(
                conversation_id=self.conversation_id
            ).aggregate(latest=models.Max('created_at'))['latest']
            now = timezone.now()
            if latest is not None and latest >= now:
                now = latest + timedelta(microseconds=1)
            self.created_at = now
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.get_sender_type_display()}: {self.content[:50]}'
