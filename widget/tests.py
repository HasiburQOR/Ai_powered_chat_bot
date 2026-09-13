import re
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from conversations.models import Customer, Message
from platforms.models import Channel


class WidgetChatOpenTests(TestCase):
    """The widget opens straight into the chat — there is no pre-chat form.
    Identity (visitor id) and travel details are collected from the
    conversation itself."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Help Site",
            channel_type="wordpress",
            is_active=True,
            credentials={"site_key": "sk-test", "welcome_message": "Hello!"},
        )

    def _visit(self):
        return self.client.get(reverse("widget-chat"), {"site_key": "sk-test"})

    def _session_id(self) -> str:
        return self.client.cookies["widget_session_sk-test"].value

    def _poll_url(self, fragment: str) -> str:
        """Pull the poller's hx-get URL out of a /send/ response."""
        match = re.search(r'hx-get="([^"]+)"', fragment)
        self.assertIsNotNone(match, "send response must contain the poller fragment")
        return match.group(1)

    def test_first_visit_opens_chat_directly(self):
        resp = self._visit()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'id="chat-log"')
        self.assertContains(resp, "Hello!")  # welcome message present
        self.assertNotContains(resp, "/details/")
        self.assertNotContains(resp, 'name="email"')

        # A page view alone must not create any rows — drive-by iframe loads
        # would otherwise flood the dashboard with empty visitor threads.
        self.assertFalse(Customer.objects.filter(channel=self.channel).exists())

    def test_travel_intent_triggers_scripted_questions_once(self):
        """The bot asks for travel details only after the visitor shows intent,
        and never repeats the question list. With the async pipeline, /send/
        answers instantly with the typing-poller fragment and the bubbles
        arrive via /poll/."""
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "I'm looking for a 7 days Dubai package"},
        )
        body = resp.content.decode()
        self.assertIn("widget-poll", body)  # typing indicator with polling
        self.assertNotIn('class="msg-row bot"', body)  # replies come via /poll/

        poll_body = self.client.get(self._poll_url(body)).content.decode()
        self.assertEqual(poll_body.count('class="msg-row bot"'), 2)  # reply + questions
        self.assertIn("WhatsApp number", poll_body)

        # A follow-up still carrying intent never repeats the list.
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Also maybe a 5 star hotel"},
        )
        poll_body = self.client.get(
            self._poll_url(resp.content.decode())).content.decode()
        self.assertEqual(poll_body.count('class="msg-row bot"'), 1)
        self.assertNotIn("WhatsApp number", poll_body)

    def test_send_message_fragment_has_no_visitor_bubble(self):
        """Optimistic UI: chat.html adds the visitor's bubble client-side the
        instant they submit, so NO server fragment may contain it — /send/
        returns the poller, /poll/ returns only bot bubbles."""
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Hi there"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("msg-row visitor", body)  # visitor bubble is client-side
        self.assertIn("widget-poll", body)         # typing indicator with polling
        poll_body = self.client.get(self._poll_url(body)).content.decode()
        self.assertNotIn("msg-row visitor", poll_body)
        self.assertIn("msg-row bot", poll_body)    # bot reply appended server-side
        # No travel intent → reply only; visitor + reply = 2 messages.
        self.assertEqual(
            Message.objects.filter(conversation__customer__channel=self.channel).count(), 2,
        )

    def test_bot_name_from_credentials_with_default_fallback(self):
        """The widget header shows the channel's bot_name credential (per-site
        branding), never the admin-facing channel name; unset → 'Assistant'."""
        Channel.objects.create(
            name="Branded Site",
            channel_type="wordpress",
            is_active=True,
            credentials={"site_key": "sk-branded", "bot_name": "NovaBot"},
        )
        resp = self.client.get(reverse("widget-chat"), {"site_key": "sk-branded"})
        self.assertContains(resp, "NovaBot")          # branded name in the header
        self.assertNotContains(resp, "Branded Site")  # admin label doesn't leak

        resp = self.client.get(reverse("widget-chat"), {"site_key": "sk-test"})
        self.assertContains(resp, ">Assistant<")      # default when bot_name is unset

    def test_visitor_id_keeps_one_customer_without_cookies(self):
        """Browsers drop cookies inside third-party iframes; the host page's
        localStorage-backed ?v= id must keep the same visitor on ONE Customer
        with ONE Conversation across visits."""
        vid = "v1s2t3u4v5w6x7y8"
        self.client.get(reverse("widget-chat"), {"site_key": "sk-test", "v": vid})
        self.client.post(
            reverse("widget-send", args=[vid]),
            {"site_key": "sk-test", "message": "Hi again"},
        )

        # Second visit carrying the same v id but NO cookies at all (fresh
        # client simulates a cookie-blocking third-party iframe).
        cookieless = Client()
        resp = cookieless.get(reverse("widget-chat"), {"site_key": "sk-test", "v": vid})
        self.assertNotContains(resp, "/details/")
        self.assertContains(resp, "Hi again")      # prior history still visible

        self.assertEqual(Customer.objects.filter(channel=self.channel).count(), 1)
        customer = Customer.objects.get(channel=self.channel)
        self.assertEqual(customer.conversations.count(), 1)
        # Visitor + bot reply ("Hi again" carries no travel intent).
        self.assertEqual(customer.conversations.get().messages.count(), 2)

    def test_garbage_session_id_is_rejected(self):
        """The <str:session_id> converter accepts anything; junk must never
        reach external_id (a wrong form action once created a customer named
        "details")."""
        resp = self.client.post(
            reverse("widget-send", args=["!!!not-an-id!!!"]),
            {"site_key": "sk-test", "message": "Hi"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Customer.objects.filter(channel=self.channel).exists())

    def test_reply_fragment_renders_bubbles_and_no_template_comment_leak(self):
        """Regression: the multi-line `{# #}` comment in bubbles.html once
        leaked into the live widget as literal chat text — Django `{# #}`
        comments cannot span multiple lines. BOTH fragments (the /send/
        poller and the /poll/ bubbles) must contain only markup, never
        comment text."""
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Hi, do you have Dubai packages?"},
        )
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn("#}", body)           # poller fragment is leak-free too
        self.assertNotIn("Optimistic UI", body)

        poll_body = self.client.get(self._poll_url(body)).content.decode()
        self.assertNotIn("Optimistic UI", poll_body)
        self.assertNotIn("HTMX response for", poll_body)
        self.assertNotIn("#}", poll_body)
        # Travel intent → the reply bubble + the scripted profile questions.
        self.assertEqual(poll_body.count('class="msg-row bot"'), 2)
        self.assertIn("WhatsApp number", poll_body)

    @patch("widget.views.process_widget_message")
    def test_send_falls_back_to_inline_processing_when_broker_is_down(self, mock_task):
        """A Redis/Celery outage must never eat a message: /send/ then runs the
        engine inline and returns the bubbles directly (the old behaviour)."""
        mock_task.delay.side_effect = ConnectionError("redis down")
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Hi, do you have Dubai packages?"},
        )
        body = resp.content.decode()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(body.count('class="msg-row bot"'), 2)  # reply + questions, inline
        self.assertIn("WhatsApp number", body)

    def test_poll_endpoint_validates_session_site_and_timestamp(self):
        """The poller is a public GET endpoint: junk sessions, wrong site keys
        and garbage watermarks are rejected; a valid poll before any send
        simply re-arms the poller."""
        self._visit()
        session_id = self._session_id()
        good_after = {"site_key": "sk-test", "after": "2026-09-13T10:00:00+00:00"}

        junk_session = self.client.get(
            reverse("widget-poll", args=["!!!junk!!!"]), good_after)
        self.assertEqual(junk_session.status_code, 403)

        bad_after = self.client.get(
            reverse("widget-poll", args=[session_id]),
            {"site_key": "sk-test", "after": "not-a-date"})
        self.assertEqual(bad_after.status_code, 403)

        bad_site = self.client.get(
            reverse("widget-poll", args=[session_id]),
            {"site_key": "sk-wrong", "after": "2026-09-13T10:00:00+00:00"})
        self.assertEqual(bad_site.status_code, 403)

        ok = self.client.get(reverse("widget-poll", args=[session_id]), good_after)
        self.assertEqual(ok.status_code, 200)
        self.assertIn("widget-poll", ok.content.decode())  # still polling, nothing yet


class WidgetBrandingAndComposerTests(TestCase):
    """TavelDoor logo branding + the multi-line composer
    (plain Enter sends, Shift+Enter inserts a newline)."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Help Site",
            channel_type="wordpress",
            is_active=True,
            credentials={"site_key": "sk-ui", "bot_name": "TavelDoor Assistant"},
        )

    def test_chat_header_shows_traveldoor_logo(self):
        resp = self.client.get(reverse("widget-chat"), {"site_key": "sk-ui"})
        self.assertContains(resp, "chat-logo")
        self.assertContains(resp, "/static/img/logo.webp")

    def test_composer_is_textarea_with_enter_to_send_script(self):
        """Shift+Enter can only work with a <textarea> + the keydown handler
        that submits on plain Enter."""
        resp = self.client.get(reverse("widget-chat"), {"site_key": "sk-ui"})
        self.assertContains(resp, "<textarea")
        self.assertNotContains(resp, "<input type=\"text\" name=\"message\"")
        self.assertContains(resp, "shiftKey")

    def test_embed_js_floating_button_uses_logo(self):
        resp = self.client.get(reverse("widget-embed-js"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "logo.webp")
        self.assertNotContains(resp, "&#128172;")  # old 💬 emoji is gone
