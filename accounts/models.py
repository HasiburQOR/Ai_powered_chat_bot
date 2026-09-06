from django.conf import settings
from django.db import models


class Agent(models.Model):
    """Stub for future human handoff — model exists, UI comes later."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='agent')
    display_name = models.CharField(max_length=200)
    is_available = models.BooleanField(default=False, help_text='Unused until handoff UI ships')

    def __str__(self):
        return self.display_name
