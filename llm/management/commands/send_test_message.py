import os

from django.core.management.base import BaseCommand, CommandError

from llm.adapters import get_adapter
from llm.models import LLMConfig


class Command(BaseCommand):
    help = "Send a hardcoded test prompt through the active LLMConfig to verify the adapter layer in isolation."

    def handle(self, *args, **options):
        config = LLMConfig.objects.filter(is_active=True).first()
        if config is None:
            raise CommandError("No active LLMConfig found. Create/activate one in the admin or dashboard first.")

        self.stdout.write(f"Using config: {config.name} ({config.provider} / {config.model_name})")

        adapter = get_adapter(config)
        reply = adapter.send(
            [
                {"role": "system", "content": "You are a helpful test assistant. Reply in one short sentence."},
                {"role": "user", "content": "Say hello and confirm you are working."},
            ],
            config,
        )
        self.stdout.write(self.style.SUCCESS(f"Reply: {reply}"))
