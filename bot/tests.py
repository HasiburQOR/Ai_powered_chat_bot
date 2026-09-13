"""Bot engine tests: LLM retry ladder, intent keywords (incl. Banglish),
and profile context honesty."""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from bot.engine import _is_abusive, _profile_context, handle_inbound_message
from bot.tasks import summarize_idle_customers
from conversations.models import Conversation, Customer, Message
from knowledge.models import Rule
from llm.models import LLMConfig
from platforms.models import Channel
from profiles.models import TravelProfile

# A closed port so any (unmocked) background LLM call — e.g. inline profile
# extraction under CELERY_TASK_ALWAYS_EAGER — fails instantly instead of
# reaching the real internet.
FAST_FAIL_URL = "http://127.0.0.1:9/v1"


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
    """A transient provider hiccup must not derail the conversation: the engine
    retries on a TRIMMING ladder (full prompt → no RAG + last 5 messages →
    minimal + last 4) before the warm fallback. (In production a single
    timeout produced the 'I'm not sure about that' fallback right before the
    onboarding questions — and retrying the identical payload was guaranteed
    to fail the exact same way.)"""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        LLMConfig.objects.create(
            name="Primary", provider="openai", model_name="gpt-4o-mini",
            api_base_url=FAST_FAIL_URL, is_active=True)

    @patch("bot.engine.RETRY_BACKOFF_SECONDS", 0)
    @patch("bot.engine.get_adapter")
    def test_persistent_llm_failure_retries_then_falls_back(self, mock_get_adapter):
        conversation = self._conversation()
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = RuntimeError("provider down")

        out = handle_inbound_message(conversation, "hello there")

        self.assertEqual(adapter.send.call_count, 3)  # full → trimmed → minimal
        self.assertEqual(len(out), 1)
        # The warm fallback invites a re-send; the old "let me get a team
        # member" wording must never come back.
        self.assertNotIn("team member", out[0].content)
        self.assertIn("send your message again", out[0].content.lower())

    @patch("bot.engine.get_adapter")
    def test_llm_success_on_second_attempt_uses_real_reply(self, mock_get_adapter):
        conversation = self._conversation()
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = [RuntimeError("blip"), "Sure, happy to help!"]

        out = handle_inbound_message(conversation, "hello again")

        self.assertEqual(adapter.send.call_count, 2)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].content, "Sure, happy to help!")  # not the fallback

    @patch("bot.engine.RETRY_BACKOFF_SECONDS", 0)
    @patch("bot.engine.get_adapter")
    def test_empty_llm_content_is_retried_not_sent(self, mock_get_adapter):
        """Reasoning models sometimes answer HTTP 200 with EMPTY content — the
        old code returned that as a valid reply and the bot 'said nothing'.
        Empty must be treated like a failure and retried."""
        conversation = self._conversation()
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = ["", "   ", "A real answer"]

        out = handle_inbound_message(conversation, "hello there")

        self.assertEqual(adapter.send.call_count, 3)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].content, "A real answer")

    @patch("bot.engine.RETRY_BACKOFF_SECONDS", 0)
    @patch("bot.engine.get_adapter")
    def test_retries_trim_the_context_each_attempt(self, mock_get_adapter):
        """Attempt 2 drops the RAG excerpts (an oversized/awkward context is a
        common failure cause), attempt 3 is minimal — last 4 turns only."""
        conversation = self._conversation()
        for i in range(6):
            Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.CUSTOMER,
                content=f"older message {i}")
        adapter = mock_get_adapter.return_value
        adapter.send.side_effect = RuntimeError("still down")
        fake_chunk = SimpleNamespace(title="Visa info", content="Visa on arrival available.")

        with patch("bot.engine.retrieve_relevant_chunks", return_value=[fake_chunk]):
            handle_inbound_message(conversation, "hello there")

        payloads = [call.args[0] for call in adapter.send.call_args_list]
        self.assertEqual(len(payloads), 3)

        def rag_count(payload):
            return sum(1 for m in payload
                       if "Relevant knowledge base excerpts" in m.get("content", ""))

        self.assertEqual(rag_count(payloads[0]), 1)  # attempt 1: full context with RAG
        self.assertEqual(rag_count(payloads[1]), 0)  # attempt 2: RAG dropped
        self.assertEqual(rag_count(payloads[2]), 0)  # attempt 3: minimal
        # The prompt shrinks monotonically as the ladder descends
        # (6 history turns → 5 → 4).
        self.assertLess(len(payloads[1]), len(payloads[0]))
        self.assertLess(len(payloads[2]), len(payloads[1]))


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

    @patch("bot.engine.get_adapter")
    def test_banglish_armenia_message_triggers_intro(self, mock_get_adapter):
        """'ami armeniya gurte jete chai for 5 days' (romanized Bangla for
        'I want to go to Armenia for 5 days') — the real visitor message that
        used to produce a confused 'I'm not sure' reply because intent
        detection was English-only and the spelling 'armeniya' was missing."""
        conversation = self._conversation()
        mock_get_adapter.return_value.send.return_value = "অবশ্যই, আপনাকে সাহায্য করতে পারি!"

        out = handle_inbound_message(
            conversation, "ami armeniya gurte jete chai for 5 days")

        self.assertEqual(len(out), 2)  # reply + scripted questions
        profile = TravelProfile.objects.get(customer=conversation.customer)
        self.assertTrue(profile.travel_intent_detected)

    @patch("bot.engine.get_adapter")
    def test_prompt_instructs_llm_on_banglish_and_mixed_language(self, mock_get_adapter):
        """The per-turn language instruction must explicitly cover romanized
        Bangla / mixed-language sentences, not only distinct scripts."""
        conversation = self._conversation()
        mock_get_adapter.return_value.send.return_value = "ok"

        handle_inbound_message(conversation, "hi there")

        messages = mock_get_adapter.return_value.send.call_args.args[0]
        system_texts = [m["content"] for m in messages if m["role"] == "system"]
        self.assertTrue(
            any("Banglish" in t and "ami armeniya gurte jete chai" in t
                for t in system_texts),
            "the language instruction must teach Banglish/mixed-language handling")


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


class EngineAbuseTests(EngineTestMixin, TestCase):
    """Abusive turns must never draw the clueless 'let me get a team member'
    fallback: the LLM is coached to de-escalate, and when it still cannot
    answer (provider refusal / outage), a calm built-in reply goes out."""

    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        LLMConfig.objects.create(
            name="Primary", provider="openai", model_name="gpt-4o-mini",
            api_base_url=FAST_FAIL_URL, is_active=True)

    @patch("bot.engine.get_adapter")
    def test_abusive_message_coaches_llm_to_deescalate(self, mock_get_adapter):
        conversation = self._conversation()
        mock_get_adapter.return_value.send.return_value = (
            "Sorry you're feeling this way — how can I help with your trip?")

        out = handle_inbound_message(conversation, "fuck you")

        self.assertEqual(len(out), 1)
        self.assertNotIn("team member", out[0].content)
        messages = mock_get_adapter.return_value.send.call_args.args[0]
        system_texts = [m["content"] for m in messages if m["role"] == "system"]
        self.assertTrue(
            any("ABUSE HANDLING" in t for t in system_texts),
            "the de-escalation instruction must reach the LLM")

    @patch("bot.engine.RETRY_BACKOFF_SECONDS", 0)
    @patch("bot.engine.get_adapter")
    def test_abusive_message_llm_failure_gets_calm_reply(self, mock_get_adapter):
        """Provider refuses/abuses-filter blocks the call → the calm built-in
        reply, never 'let me get a team member to help.'"""
        conversation = self._conversation()
        mock_get_adapter.return_value.send.side_effect = RuntimeError("refused")

        out = handle_inbound_message(conversation, "fuck you")

        self.assertEqual(mock_get_adapter.return_value.send.call_count, 3)  # ladder still applies
        self.assertEqual(len(out), 1)
        self.assertNotIn("team member", out[0].content)
        self.assertIn("help", out[0].content.lower())

    @patch("bot.engine.RETRY_BACKOFF_SECONDS", 0)
    @patch("bot.engine.get_adapter")
    def test_non_abusive_failure_keeps_normal_fallback(self, mock_get_adapter):
        """A plain (non-abusive) message gets the WARM fallback when the
        provider is down — never the old clueless 'team member' wording."""
        conversation = self._conversation()
        mock_get_adapter.return_value.send.side_effect = RuntimeError("down")

        out = handle_inbound_message(conversation, "hello there")

        self.assertEqual(mock_get_adapter.return_value.send.call_count, 3)
        self.assertNotIn("team member", out[0].content)
        self.assertIn("send your message again", out[0].content.lower())

    def test_detection_boundaries(self):
        self.assertTrue(_is_abusive("fuck you"))
        self.assertTrue(_is_abusive("You are USELESS!!"))
        self.assertTrue(_is_abusive("f u c k you"))  # obfuscated
        self.assertTrue(_is_abusive("f.u.c.k"))      # obfuscated
        # Word-start guard keeps innocents out (Scunthorpe contains c·u·n·t),
        # even in very short messages where a naive "remove spaces" check
        # would resurrect the false positive.
        self.assertFalse(_is_abusive("Do you have Scunthorpe tours?"))
        self.assertFalse(_is_abusive("Scunthorpe?"))
        self.assertFalse(_is_abusive("What is the price of the Dubai package?"))


class MemorySummarizerTests(EngineTestMixin, TestCase):
    """Idle-customer summarization: the beat task refreshes long-term memory
    for visitors who went quiet (the message-count trigger only fires while
    they are actively chatting), skipping anyone already current."""

    @patch("bot.tasks.summarize_customer_memory.delay")
    def test_idle_customer_in_window_gets_summarized(self, mock_delay):
        customer = Customer.objects.create(channel=self.channel, external_id="idle-1")
        Conversation.objects.create(
            customer=customer, last_message_at=timezone.now() - timedelta(hours=2))

        summarize_idle_customers()

        mock_delay.assert_called_once_with(str(customer.pk))

    @patch("bot.tasks.summarize_customer_memory.delay")
    def test_active_and_long_gone_customers_are_skipped(self, mock_delay):
        active = Customer.objects.create(channel=self.channel, external_id="active")
        Conversation.objects.create(customer=active, last_message_at=timezone.now())
        gone = Customer.objects.create(channel=self.channel, external_id="gone")
        Conversation.objects.create(
            customer=gone, last_message_at=timezone.now() - timedelta(days=3))

        summarize_idle_customers()

        mock_delay.assert_not_called()

    @patch("bot.tasks.summarize_customer_memory.delay")
    def test_customer_with_fresh_summary_is_skipped(self, mock_delay):
        customer = Customer.objects.create(
            channel=self.channel, external_id="summed",
            memory_summary="Wants a Dubai tour",
            memory_summary_at=timezone.now() - timedelta(minutes=10))
        Conversation.objects.create(
            customer=customer, last_message_at=timezone.now() - timedelta(hours=1))

        summarize_idle_customers()

        mock_delay.assert_not_called()

    @patch("llm.adapters.get_adapter")
    def test_shared_summarizer_stamps_memory_summary_at(self, mock_get_adapter):
        """Both trigger paths share _summarize_customer; storing a summary must
        also stamp memory_summary_at so the beat task skips them afterwards.
        An empty provider reply must not flag anything."""
        from bot.tasks import _summarize_customer

        LLMConfig.objects.create(
            name="Primary", provider="openai", model_name="gpt-4o-mini",
            api_base_url=FAST_FAIL_URL, is_active=True)
        customer = Customer.objects.create(channel=self.channel, external_id="sum-1")
        conversation = Conversation.objects.create(
            customer=customer, last_message_at=timezone.now())
        Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.CUSTOMER, content="I love Dubai tours")
        mock_get_adapter.return_value.send.return_value = "Wants a Dubai tour."

        self.assertTrue(_summarize_customer(customer))
        customer.refresh_from_db()
        self.assertEqual(customer.memory_summary, "Wants a Dubai tour.")
        self.assertIsNotNone(customer.memory_summary_at)

        mock_get_adapter.return_value.send.return_value = "   "
        self.assertFalse(_summarize_customer(customer))
