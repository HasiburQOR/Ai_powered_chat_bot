"""Travel-profile capture: numbering, LLM extraction, engine intro bubble."""
import datetime as dt
from io import BytesIO
from unittest import mock

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from bot.engine import handle_inbound_message
from conversations.models import Conversation, Customer, Message
from knowledge.models import BotSettings, Rule
from platforms.models import Channel
from profiles.extraction import apply_fields, extract_fields, parse_extraction_json
from profiles.models import ProfileNumberCounter, TravelProfile
from profiles.tasks import extract_profile_task


class ProfileNumberTests(TestCase):
    def test_numbers_increment_and_format(self):
        self.assertEqual(ProfileNumberCounter.next_profile_number(), "BP-000001")
        self.assertEqual(ProfileNumberCounter.next_profile_number(), "BP-000002")
        self.assertEqual(TravelProfile.objects.count(), 0)  # numbers are minted independently


class ExtractionTests(TestCase):
    def test_parse_tolerates_code_fences_and_prose(self):
        raw = 'Sure! ```json\n{"full_name": "Ravi"}\n``` hope that helps'
        self.assertEqual(parse_extraction_json(raw), {"full_name": "Ravi"})

    def test_parse_garbage_returns_empty(self):
        self.assertEqual(parse_extraction_json("no json here"), {})
        self.assertEqual(parse_extraction_json(""), {})

    def test_extract_fields_coerces_and_drops_invalid(self):
        def fake_llm(messages, config):
            return ('```json\n{"full_name": "Ravi", "trip_days": "12", "adults": 2, '
                    '"travel_date": "2026-10-12", "junk": "x"}\n```')

        fields = extract_fields("my name is Ravi, 12 days, 2 adults", send_fn=fake_llm)
        self.assertEqual(fields, {
            "full_name": "Ravi", "trip_days": 12, "adults": 2,
            "travel_date": dt.date(2026, 10, 12),
        })

    def test_extract_fields_bad_types_dropped(self):
        fields = extract_fields("x", send_fn=lambda m, c: '{"trip_days": "soon", "adults": null}')
        self.assertEqual(fields, {})

    def test_blank_text_never_calls_llm(self):
        called = []

        def fake_llm(messages, config):
            called.append(1)
            return "{}"

        self.assertEqual(extract_fields("   ", send_fn=fake_llm), {})
        self.assertEqual(called, [])

    def test_no_active_config_returns_empty(self):
        self.assertEqual(extract_fields("hello"), {})


class ApplyFieldsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="WP", channel_type="wordpress")
        cls.customer = Customer.objects.create(channel=cls.channel, external_id="lead-1")

    def test_first_fill_creates_numbered_profile(self):
        profile = apply_fields(self.customer, {"full_name": "Ravi Kumar", "nationality": "Indian"})
        self.assertEqual(profile.profile_number, "BP-000001")
        self.assertFalse(profile.is_complete)
        self.assertEqual(profile.full_name, "Ravi Kumar")
        self.customer.refresh_from_db()
        self.assertEqual(self.customer.display_name, "Ravi Kumar")  # backfilled

    def test_second_apply_updates_same_profile_and_completes(self):
        apply_fields(self.customer, {"full_name": "Ravi"})
        profile = apply_fields(self.customer, {
            "whatsapp_number": "+911234567890", "nationality": "Indian",
            "residence_country": "India", "travel_date": dt.date(2026, 12, 1),
            "trip_days": 7, "adults": 3,
        })
        self.assertEqual(profile.profile_number, "BP-000001")
        self.assertTrue(profile.is_complete)
        self.assertIsNotNone(profile.completed_at)
        self.assertEqual(TravelProfile.objects.count(), 1)
        self.assertEqual(profile.total_travellers, 3)

    def test_children_count_toward_travellers(self):
        profile = apply_fields(self.customer, {"adults": 2, "children_ages": "5, 8"})
        self.assertEqual(profile.total_travellers, 4)

    def test_missing_fields_listing(self):
        profile = apply_fields(self.customer, {"full_name": "Ravi"})
        self.assertIn("WhatsApp number", profile.missing_fields())
        self.assertIn("Travel date", profile.missing_fields())


class ExtractionTaskTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="WP", channel_type="wordpress")
        cls.customer = Customer.objects.create(channel=cls.channel, external_id="lead-9")

    def test_task_merges_extracted_fields_into_profile(self):
        with mock.patch("profiles.extraction.LLMConfig") as fake_config_cls, \
                mock.patch("profiles.extraction.get_adapter") as fake_get_adapter:
            fake_config_cls.objects.filter.return_value.first.return_value = object()
            fake_get_adapter.return_value.send = lambda messages, config: \
                '{"full_name": "Ravi", "travel_date": "2026-10-12"}'
            extract_profile_task(str(self.customer.pk), "I am Ravi, travelling in October")

        profile = self.customer.travel_profile
        self.assertEqual(profile.full_name, "Ravi")
        self.assertEqual(profile.travel_date, dt.date(2026, 10, 12))
        self.assertEqual(profile.profile_number, "BP-000001")

    def test_task_ignores_unknown_customer_and_blank_text(self):
        extract_profile_task("00000000-0000-0000-0000-000000000000", "hi")
        extract_profile_task(str(self.customer.pk), "   ")
        self.assertFalse(TravelProfile.objects.exists())


class EngineProfileIntroTests(TestCase):
    """The scripted profile question rides along as a second bot bubble."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="WP", channel_type="wordpress")
        cls.customer = Customer.objects.create(channel=cls.channel, external_id="lead-2")
        cls.conversation = Conversation.objects.create(
            customer=cls.customer, last_message_at=timezone.now())
        Rule.objects.create(name="greet", trigger_keywords=["hello"],
                            response_text="Hello! How can I help you?",
                            short_circuits_llm=True, priority=10)

    def test_first_message_gets_reply_plus_intro(self):
        outbounds = handle_inbound_message(self.conversation, "hello")
        self.assertEqual(len(outbounds), 2)
        self.assertEqual(outbounds[0].content, "Hello! How can I help you?")
        self.assertIn("WhatsApp number", outbounds[1].content)
        self.assertTrue(all(m.sender_type == Message.SenderType.BOT for m in outbounds))

    def test_second_message_gets_reply_only(self):
        handle_inbound_message(self.conversation, "hello")
        outbounds = handle_inbound_message(self.conversation, "thanks, that's clear")
        self.assertEqual(len(outbounds), 1)

    def test_disabled_settings_skip_intro(self):
        settings = BotSettings.load()
        settings.profile_collection_enabled = False
        settings.save()
        outbounds = handle_inbound_message(self.conversation, "hello")
        self.assertEqual(len(outbounds), 1)

    def test_complete_profile_skips_intro(self):
        apply_fields(self.customer, {
            "full_name": "Ravi", "whatsapp_number": "+911234567890",
            "nationality": "Indian", "residence_country": "India",
            "travel_date": dt.date(2026, 12, 1), "trip_days": 7, "adults": 2,
        })
        outbounds = handle_inbound_message(self.conversation, "hello")
        self.assertEqual(len(outbounds), 1)

    def test_language_and_format_instructions_sent_to_llm(self):
        captured = {}

        class FakeAdapter:
            def send(self, messages, config):
                captured["messages"] = messages
                return "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?"

        from llm.models import LLMConfig
        LLMConfig.objects.create(
            name="Test", provider="anthropic", api_key="k",
            model_name="claude-sonnet-4-6", system_prompt="Be helpful.", is_active=True)
        with mock.patch("bot.engine.get_adapter", return_value=FakeAdapter()):
            outbounds = handle_inbound_message(self.conversation, "namaste, kya haal?")
        system_text = " ".join(
            m["content"] for m in captured["messages"] if m["role"] == "system")
        self.assertIn("same language", system_text)
        self.assertIn("Never output Markdown", system_text)
        self.assertEqual(outbounds[0].content, "नमस्ते! मैं आपकी कैसे मदद कर सकता हूँ?")

    def test_profile_state_included_in_llm_context(self):
        apply_fields(self.customer, {"full_name": "Ravi", "nationality": "Indian"})
        captured = {}

        class FakeAdapter:
            def send(self, messages, config):
                captured["messages"] = messages
                return "ok"

        from llm.models import LLMConfig
        LLMConfig.objects.create(
            name="Test", provider="anthropic", api_key="k",
            model_name="claude-sonnet-4-6", system_prompt="Be helpful.", is_active=True)
        with mock.patch("bot.engine.get_adapter", return_value=FakeAdapter()):
            handle_inbound_message(self.conversation, "what trips do you offer?")
        system_text = " ".join(
            m["content"] for m in captured["messages"] if m["role"] == "system")
        self.assertIn("Travel profile", system_text)
        self.assertIn("captured: Name: Ravi", system_text)
        self.assertIn("still missing", system_text)

    def test_engine_enqueues_extraction_task(self):
        with mock.patch("profiles.tasks.extract_profile_task") as fake_task:
            handle_inbound_message(self.conversation, "hello there team")
        fake_task.delay.assert_called_once()
        args = fake_task.delay.call_args[0]
        self.assertEqual(args[0], str(self.customer.pk))
        self.assertEqual(args[1], "hello there team")


class WidgetSendTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Site", channel_type="wordpress", is_active=True,
            credentials={"site_key": "sk-prof"})

    def test_first_send_renders_two_bot_bubbles_and_no_comment_leak(self):
        resp = self.client.post(
            reverse("widget-send", args=["visitor1"]),
            {"site_key": "sk-prof", "message": "hi"})
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("Optimistic UI", body)
        self.assertNotIn("#}", body)
        self.assertEqual(body.count('class="msg-row bot"'), 2)
        self.assertIn("WhatsApp number", body)

    def test_second_send_renders_one_bot_bubble(self):
        self.client.post(reverse("widget-send", args=["visitor1"]),
                         {"site_key": "sk-prof", "message": "hi"})
        resp = self.client.post(reverse("widget-send", args=["visitor1"]),
                                {"site_key": "sk-prof", "message": "ok thanks"})
        self.assertEqual(resp.content.decode().count('class="msg-row bot"'), 1)


class DashboardProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(name="WP", channel_type="wordpress")
        cls.staff = User.objects.create_user("staff", password="x", is_staff=True)

    def setUp(self):
        self.client = Client(HTTP_HOST="localhost")
        self.client.force_login(self.staff)

    def _make_profile(self, external_id="lead-1", **overrides):
        customer = Customer.objects.create(channel=self.channel, external_id=external_id)
        conversation = Conversation.objects.create(
            customer=customer, last_message_at=timezone.now())
        Message.objects.create(conversation=conversation,
                               sender_type=Message.SenderType.CUSTOMER,
                               content="Hi, I want a 7 days Dubai package")
        Message.objects.create(conversation=conversation,
                               sender_type=Message.SenderType.BOT,
                               content="Sure! May I have your name and WhatsApp number?")
        fields = {
            "full_name": "Ravi Kumar", "whatsapp_number": "+911234567890",
            "nationality": "Indian", "residence_country": "India",
            "gcc_residence_card": True, "residence_card_expiry": dt.date(2027, 1, 31),
            "travel_date": dt.date(2026, 12, 1), "trip_days": 7, "adults": 2,
            "children_ages": "5, 8",
        }
        fields.update(overrides)
        return apply_fields(customer, fields)

    def test_list_shows_profiles(self):
        profile = self._make_profile()
        resp = self.client.get(reverse("dashboard-profiles"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, profile.profile_number)
        self.assertContains(resp, "Ravi Kumar")

    def test_filters_by_completeness_and_query(self):
        complete = self._make_profile(external_id="lead-a")
        partial = self._make_profile(
            external_id="lead-b", full_name="Partial Pete", whatsapp_number="",
            nationality="", residence_country="", travel_date=None,
            trip_days=None, adults=None, children_ages="")
        resp = self.client.get(reverse("dashboard-profiles"), {"complete": "yes"})
        self.assertContains(resp, complete.profile_number)
        self.assertNotContains(resp, partial.profile_number)
        resp = self.client.get(reverse("dashboard-profiles"), {"complete": "no"})
        self.assertContains(resp, partial.profile_number)
        self.assertNotContains(resp, complete.profile_number)
        resp = self.client.get(reverse("dashboard-profiles"), {"q": "Ravi"})
        self.assertContains(resp, complete.profile_number)
        self.assertNotContains(resp, partial.profile_number)

    def test_csv_export(self):
        profile = self._make_profile()
        resp = self.client.get(reverse("dashboard-profile-export-csv"))
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment", resp["Content-Disposition"])
        body = resp.content.decode()
        self.assertIn("Profile Number", body)
        self.assertIn(profile.profile_number, body)
        self.assertIn("Ravi Kumar", body)

    def test_xlsx_export(self):
        from openpyxl import load_workbook

        profile = self._make_profile()
        resp = self.client.get(reverse("dashboard-profile-export-xlsx"))
        self.assertIn("spreadsheetml", resp["Content-Type"])
        wb = load_workbook(BytesIO(resp.content))
        ws = wb.active
        self.assertEqual(ws.cell(row=1, column=1).value, "Profile Number")
        self.assertEqual(ws.cell(row=2, column=1).value, profile.profile_number)
        self.assertEqual(ws.cell(row=2, column=2).value, "Ravi Kumar")

    def test_card_and_transcript_png_downloads(self):
        profile = self._make_profile()
        resp = self.client.get(reverse("dashboard-profile-card", args=[profile.pk]))
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp.content.startswith(b"\x89PNG"))
        self.assertIn("BP-000001_Ravi_Kumar.png", resp["Content-Disposition"])
        resp = self.client.get(reverse("dashboard-profile-transcript", args=[profile.pk]))
        self.assertEqual(resp["Content-Type"], "image/png")
        self.assertTrue(resp.content.startswith(b"\x89PNG"))
        self.assertIn("BP-000001_Ravi_Kumar_transcript.png", resp["Content-Disposition"])

    def test_detail_page_renders_and_edit_saves(self):
        profile = self._make_profile()
        resp = self.client.get(reverse("dashboard-profile-detail", args=[profile.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ravi Kumar")
        self.assertContains(resp, "Profile card (PNG)")
        resp = self.client.post(reverse("dashboard-profile-detail", args=[profile.pk]), {
            "full_name": "Ravi K.", "whatsapp_number": "+911234567890",
            "nationality": "Indian", "residence_country": "India",
            "gcc_residence_card": "true", "residence_card_expiry": "2027-01-31",
            "travel_date": "2026-12-01", "trip_days": "7", "adults": "2",
            "children_ages": "5, 8",
        })
        self.assertEqual(resp.status_code, 302)
        profile.refresh_from_db()
        self.assertEqual(profile.full_name, "Ravi K.")

    def test_profile_pages_require_staff(self):
        profile = self._make_profile()
        anonymous = Client(HTTP_HOST="localhost")
        for url in (
            reverse("dashboard-profiles"),
            reverse("dashboard-profile-detail", args=[profile.pk]),
            reverse("dashboard-profile-card", args=[profile.pk]),
            reverse("dashboard-profile-transcript", args=[profile.pk]),
            reverse("dashboard-profile-export-csv"),
            reverse("dashboard-profile-export-xlsx"),
        ):
            self.assertEqual(anonymous.get(url).status_code, 302, url)