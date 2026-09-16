"""Deepen the bot's conversation memory in existing deployments.

Production chats showed the bot "forgetting" anything older than ~10
messages: max_context_messages (default 10) sliced the short-term window,
and the long-term Customer.memory_summary only refreshed every 20 CUSTOMER
messages — leaving a blind gap where scrolled-off context was in neither.
The new defaults — 50 recent messages plus a summary refresh every 10
customer messages — keep older context inside the window or inside the
summary at all times (deep recall, ~4k tokens/call on long chats; the
retry ladder already trims on failure).

Like the BotSettings singleton's other defaults, the row was created via
get_or_create, so existing databases still hold the old values — only a
data migration can reach them. Rows an admin deliberately retuned in the
dashboard keep their values untouched.
"""
from django.db import migrations, models

OLD_WINDOW, NEW_WINDOW = 10, 50
OLD_TRIGGER, NEW_TRIGGER = 20, 10


def _retune(apps, from_window, to_window, from_trigger, to_trigger):
    BotSettings = apps.get_model("knowledge", "BotSettings")
    for row in BotSettings.objects.all():
        if row.max_context_messages == from_window:
            row.max_context_messages = to_window
        if row.memory_summary_trigger_count == from_trigger:
            row.memory_summary_trigger_count = to_trigger
        row.save(update_fields=["max_context_messages",
                                "memory_summary_trigger_count",
                                "updated_at"])


def deepen_memory(apps, schema_editor):
    _retune(apps, OLD_WINDOW, NEW_WINDOW, OLD_TRIGGER, NEW_TRIGGER)


def restore_shallow_memory(apps, schema_editor):
    _retune(apps, NEW_WINDOW, OLD_WINDOW, NEW_TRIGGER, OLD_TRIGGER)


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0006_shorten_profile_intro"),
    ]

    operations = [
        # Keep the recorded field state in sync with the new model defaults
        # and help text (Django's autodetector flags the change otherwise).
        migrations.AlterField(
            model_name="botsettings",
            name="max_context_messages",
            field=models.PositiveIntegerField(default=50, help_text='How many recent messages count as short-term memory. 50 = deep recall (~4k tokens/call on long chats) so the bot no longer forgets after ~10 messages; keep this above memory_summary_trigger_count so nothing falls into a gap.'),
        ),
        migrations.AlterField(
            model_name="botsettings",
            name="memory_summary_trigger_count",
            field=models.PositiveIntegerField(default=10, help_text='Regenerate Customer.memory_summary every N new CUSTOMER messages (bot bubbles do not count). Keep below max_context_messages so older context is always inside either the recent-message window or the summary.'),
        ),
        migrations.RunPython(deepen_memory, restore_shallow_memory),
    ]