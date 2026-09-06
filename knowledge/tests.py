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


class RetrievalTests(TestCase):
    def test_no_embeddings_returns_empty(self):
        # With no embedded chunks (or model unavailable), retrieval degrades gracefully.
        KnowledgeChunk.objects.create(title="t", content="c")
        from unittest.mock import patch
        from knowledge.retrieval import retrieve_relevant_chunks
        with patch("knowledge.retrieval.embed_text", return_value=None):
            self.assertEqual(list(retrieve_relevant_chunks("anything")), [])
