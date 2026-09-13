"""Shorten the scripted profile opener in existing deployments.

The old default fired a 5-bullet interrogation (name, WhatsApp, nationality,
residence, GCC card, dates, package length, party size...) the moment a
visitor showed travel interest — the exact "bot asks random questions /
interrogates me" complaint. The new default is a two-line opener (travel
date + number of travellers); the LLM now collects each remaining detail
with one short follow-up at a time. Like the BotSettings singleton's other
defaults, the row was created via get_or_create, so existing databases still
hold the old text — only a data migration can reach it.

Only rows still carrying the old default are rewritten; a custom opener
written in the dashboard is never clobbered. Keep NEW_DEFAULT in sync with
knowledge.models.PROFILE_INTRO_MESSAGE_DEFAULT.
"""
from django.db import migrations, models

OLD_DEFAULT = (
    "May I have the following information?\n"
    "- Name and WhatsApp number?\n"
    "- Nationality and country of residence? If you have a GCC residence card, its expiry date too.\n"
    "- Approximate travel date?\n"
    "- How many days package are you looking for?\n"
    "- How many people are travelling together? For children, please share their ages."
)

NEW_DEFAULT = (
    "Great, I can help with that! To find you the best package:\n"
    "- When are you planning to travel?\n"
    "- How many people are travelling?"
)


def shorten_intro(apps, schema_editor):
    BotSettings = apps.get_model("knowledge", "BotSettings")
    for row in BotSettings.objects.all():
        text = (row.profile_intro_message or "").strip()
        if not text or text == OLD_DEFAULT.strip() or text.startswith(
                "May I have the following information?"):
            row.profile_intro_message = NEW_DEFAULT
            row.save(update_fields=["profile_intro_message", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("knowledge", "0005_fix_stale_fallback_message"),
    ]

    operations = [
        # Keep the recorded field state in sync with the new model default
        # and help text (Django's autodetector flags the change otherwise).
        migrations.AlterField(
            model_name="botsettings",
            name="profile_intro_message",
            field=models.TextField(blank=True, default='Great, I can help with that! To find you the best package:\n- When are you planning to travel?\n- How many people are travelling?', help_text='Scripted opener, sent as a second bubble the first time a visitor shows travel intent. Keep it to one or two questions — afterwards the AI collects each remaining detail one short follow-up at a time (requires profile collection to be enabled; leave blank to let the AI ask everything itself).'),
        ),
        migrations.RunPython(shorten_intro, migrations.RunPython.noop),
    ]
