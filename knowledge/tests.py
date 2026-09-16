from django.test import TestCase

from knowledge.models import KnowledgeChunk, Rule
from knowledge.retrieval import match_rule


class RuleMatchingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        Rule.objects.create(
            name="pricing", trigger_keywords=["price", "cost"], response_text="Plans start at $10/mo.",
            short_circuits_llm=True, priority=10,
        )

    def test_keyword_match_is_case_insensitive(self):
        rule = match_rule("How much does it COST?")
        self.assertIsNotNone(rule)
        self.assertEqual(rule.name, "pricing")

    def test_no_match_returns_none(self):
        self.assertIsNone(match_rule("Tell me about pandas."))

    def test_short_keyword_does_not_match_inside_words(self):
        """Regression: a Welcome rule keyed on "hi" fired for a visitor message
        containing "chinlder" (typo for children), greeting them in the middle
        of sharing travel details. Matching must respect word boundaries."""
        Rule.objects.create(
            name="Welcome", trigger_keywords=["hi", "hello"],
            response_text="Hi there!", short_circuits_llm=True, priority=1)
        self.assertIsNone(match_rule(
            "have dont gcc, residence card: 2 years, 3 novembor, "
            "7 days trip, 4 people 1 chinlder 1 year old"))

    def test_keyword_still_matches_as_whole_word(self):
        Rule.objects.create(
            name="Welcome", trigger_keywords=["hi", "hello"],
            response_text="Hi there!", short_circuits_llm=True, priority=1)
        for text in ("Hi there", "hi! any offers?", "Hello"):
            self.assertIsNotNone(match_rule(text), text)

    def test_multiword_keyword_respects_boundaries(self):
        Rule.objects.create(
            name="Money back", trigger_keywords=["money back"],
            response_text="Refunds are fine.", short_circuits_llm=True, priority=1)
        self.assertIsNotNone(match_rule("Can I get my money back?"))
        self.assertIsNone(match_rule("welcome to the moneybackmarket"))


class RetrievalTests(TestCase):
    def test_no_embeddings_returns_empty(self):
        # With no embedded chunks (or model unavailable), retrieval degrades gracefully.
        KnowledgeChunk.objects.create(title="t", content="c")
        from unittest.mock import patch
        from knowledge.retrieval import retrieve_relevant_chunks
        with patch("knowledge.retrieval.embed_text", return_value=None):
            self.assertEqual(list(retrieve_relevant_chunks("anything")), [])


class DeepContextDefaultsMigrationTests(TestCase):
    """The 0007 data migration must reach EXISTING databases: the BotSettings
    row is created via get_or_create, so raising the model defaults alone
    never touches a live deployment still holding max_context_messages=10 /
    memory_summary_trigger_count=20 (the 'bot forgets after ~10 messages'
    state). Dashboard-tuned values must survive untouched."""

    def setUp(self):
        super().setUp()
        # BotSettings is cached ~30s and the locmem cache is NOT rolled back
        # between tests — keep this class from seeing (or poisoning) others.
        from django.core.cache import cache
        from knowledge.models import BOT_SETTINGS_CACHE_KEY
        cache.delete(BOT_SETTINGS_CACHE_KEY)

    def test_old_defaults_are_bumped_dashboard_values_survive(self):
        import importlib
        from django.apps import apps as global_apps
        from knowledge.models import BotSettings
        migration = importlib.import_module(
            "knowledge.migrations.0007_deep_context_defaults")

        row = BotSettings.load()
        row.max_context_messages = 10          # the old shipped default
        row.memory_summary_trigger_count = 20  # the old shipped default
        row.save()

        migration.deepen_memory(global_apps, None)

        row.refresh_from_db()
        self.assertEqual(row.max_context_messages, 50)
        self.assertEqual(row.memory_summary_trigger_count, 10)

        # A row an admin deliberately retuned in the dashboard must never
        # be clobbered by the migration.
        row.max_context_messages = 15
        row.memory_summary_trigger_count = 12
        row.save()
        migration.deepen_memory(global_apps, None)
        row.refresh_from_db()
        self.assertEqual(row.max_context_messages, 15)
        self.assertEqual(row.memory_summary_trigger_count, 12)
