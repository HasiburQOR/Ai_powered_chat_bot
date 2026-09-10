"""Bot engine tests: LLM retry, intent keywords, and profile context honesty."""
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bot.engine import _profile_context, handle_inbound_message
from conversations.models import Conversation, Customer
from knowledge.models import Rule
from llm.models import LLMConfig
from platforms.models import Channel
from profiles.models import TravelProfile


class EngineTestMixin:
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Web", channel_type="wordpress", is_active=True)

    def _conversation(self) -> Conversation:
        customer = Customer.objects.create(
            channel=self.channel, external_id="visitor-1")
        return Conversation.objects.create(
            customer=customer, last_message_at=timezone.now())


class EngineLLMRetryTests(EngineTestMixin, TestCase):
    """A transient provider hiccup must not derail the conversation: retry
    once before falling back. (In production a single timeout produced the
    'I'm not sure about that' fallback right before the onboarding questions,
    which read as the bot ignoring the visitor.)"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        LLMConfig.objects.create(
            name="Primary", provider="openai", model_name="gpt-4o-mini",
            is_active=True)

    @patch("bot.engine.get_adapter")
    def test_persistent_llm_failure_retries_then_falls_back(self, mock_get_adapter):
        conversation = self._conversation()
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = RuntimeError("provider down")

        out = handle_inbound_message(conversation, "hello there")

        self.assertEqual(adapter.send.call_count, 2)  # retried once
        self.assertEqual(len(out), 1)
        self.assertIn("team member", out[0].content)  # fallback text

    @patch("bot.engine.get_adapter")
    def test_llm_success_on_second_attempt_uses_real_reply(self, mock_get_adapter):
        conversation = self._conversation()
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = [RuntimeError("blip"), "Sure, happy to help!"]

        out = handle_inbound_message(conversation, "hello again")

        self.assertEqual(adapter.send.call_count, 2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].content, "Sure, happy to help!")  # not the fallback


class EngineProfileIntroTests(EngineTestMixin, TestCase):
    """Intent detection and the once-only scripted questions."""

    @patch("bot.engine.get_adapter")
    def test_armania_misspelling_still_triggers_intro(self, mock_get_adapter):
        # A real visitor wrote "armania"; the keyword list must catch it.
        conversation = self._conversation()
        mock_get_adapter.return_value.send.return_value = "Sure!"

        out = handle_inbound_message(conversation, "i want to travel to armania")

        self.assertEqual(len(out), 2)  # reply + scripted questions
        joined = "\n".join(m.content for m in out)
        self.assertIn("WhatsApp", joined)
        profile = TravelProfile.objects.get(customer=conversation.customer)
        self.assertTrue(profile.travel_intent_detected)
        self.assertIsNotNone(profile.questions_sent_at)


class ProfileContextTests(EngineTestMixin, TestCase):
    def test_context_tells_llm_to_trust_history_over_stale_summary(self):
        """The extractor lags behind: when the summary says a field is still
        missing but the visitor just gave it, the LLM must not re-ask."""
        customer = Customer.objects.create(
            channel=self.channel, external_id="visitor-2")
        TravelProfile.objects.create(
            customer=customer, profile_number="BP-000001", full_name="Hasibur")

        ctx = _profile_context(customer)

        self.assertIn("Name: Hasibur", ctx)
        self.assertIn("LAG", ctx)
        self.assertIn("NEVER ask for the same detail again", ctx)

    def test_empty_profile_gives_no_context(self):
        customer = Customer.objects.create(
            channel=self.channel, external_id="visitor-3")
        self.assertEqual(_profile_context(customer), "")


class EngineRuleOrderTests(EngineTestMixin, TestCase):
    @patch("bot.engine.get_adapter")
    def test_short_circuit_rule_wins_over_llm(self, mock_get_adapter):
        conversation = self._conversation()
        Rule.objects.create(
            name="Welcome", trigger_keywords=["hi", "hello"],
            response_text="Hi there! 👋 Welcome!", short_circuits_llm=True,
            priority=1)

        out = handle_inbound_message(conversation, "Hi there")

        self.assertEqual(mock_get_adapter.call_count, 0)  # LLM skipped
        self.assertEqual(out[0].content, "Hi there! 👋 Welcome!")
        # The un-matched rule path must not be triggered by words CONTAINING
        # "hi" — that regression is covered in knowledge/tests.py.
