"""Repair the stale fallback_message row in existing deployments.

The BotSettings singleton (pk=1) is created via get_or_create, so rows created
before the wording change still hold the old default — "I'm not sure about
that — let me get a team member to help." — which reads as the bot being
oblivious and kills travel leads. The code-level default was replaced in
commit 0366ae5, but get_or_create never updates an existing row, so only a
data migration can reach it.

Only rows still carrying the old default (or any "team member" wording) are
rewritten; a custom message written in the dashboard is never clobbered.
Keep NEW_DEFAULT in sync with knowledge.models.FALLBACK_MESSAGE_DEFAULT.
"""
from django.db import migrations

NEW_DEFAULT = (
    "Sorry, I couldn't process that just now — please send your message again. "
    "Meanwhile, tell me your destination, travel dates and number of travellers, "
    "and I'll get the details ready for you."
)


def repair_stale_fallback(apps, schema_editor):
    BotSettings = apps.get_model("knowledge", "BotSettings")
    for row in BotSettings.objects.all():
        text = (row.fallback_message or "").strip()
        if not text or "team member" in text.lower():
            row.fallback_message = NEW_DEFAULT
            row.save(update_fields=["fallback_message", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0004_fallback_help_text_hnsw_index"),
    ]

    operations = [
        migrations.RunPython(repair_stale_fallback, migrations.RunPython.noop),
    ]
