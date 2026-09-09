from django.test import Client, TestCase
from django.urls import reverse

from conversations.models import Customer, Message
from platforms.models import Channel


class WidgetLeadDetailsTests(TestCase):
    """Pre-chat visitor details capture (widget) wired to Customer fields."""

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

    def test_first_visit_shows_lead_form(self):
        resp = self._visit()
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "/details/")
        self.assertContains(resp, 'name="name"')
        self.assertContains(resp, 'name="email"')
        self.assertContains(resp, 'name="phone"')
        self.assertNotContains(resp, 'id="chat-log"')

        # A page view alone must not create any rows — drive-by iframe loads
        # would otherwise flood the dashboard with empty visitor threads.
        self.assertFalse(Customer.objects.filter(channel=self.channel).exists())

    def test_submit_details_saves_customer_and_swaps_in_chat(self):
        self._visit()
        resp = self.client.post(
            reverse("widget-details", args=[self._session_id()]),
            {"site_key": "sk-test", "name": "Jane Doe", "email": "jane@example.com", "phone": "+15551234"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertIn('id="chat-log"', body)
        self.assertIn("Hello!", body)  # welcome message present
        self.assertNotIn("/details/", body)  # lead form replaced by the chat panel

        customer = Customer.objects.get(channel=self.channel)
        self.assertEqual(customer.display_name, "Jane Doe")
        self.assertEqual(customer.email, "jane@example.com")
        self.assertEqual(customer.phone, "+15551234")
        self.assertTrue(customer.has_contact_details)

    def test_returning_visitor_skips_lead_form(self):
        self._visit()
        self.client.post(
            reverse("widget-details", args=[self._session_id()]),
            {"site_key": "sk-test", "name": "Jane", "email": "jane@example.com"},
        )
        resp = self._visit()
        self.assertNotContains(resp, "/details/")
        self.assertContains(resp, 'id="chat-log"')

    def test_email_is_required(self):
        self._visit()
        resp = self.client.post(
            reverse("widget-details", args=[self._session_id()]),
            {"site_key": "sk-test", "name": "Jane"},  # missing email
        )
        self.assertEqual(resp.status_code, 422)
        self.assertIn("This field is required", resp.content.decode())

        customer = Customer.objects.get(channel=self.channel)
        self.assertEqual(customer.email, "")
        self.assertEqual(customer.display_name, "Website visitor")  # unchanged

    def test_channel_can_opt_out_of_lead_capture(self):
        Channel.objects.create(
            name="Legacy Site",
            channel_type="wordpress",
            is_active=True,
            credentials={"site_key": "sk-legacy", "collect_lead_details": False},
        )
        resp = self.client.get(reverse("widget-chat"), {"site_key": "sk-legacy"})
        self.assertContains(resp, 'id="chat-log"')
        self.assertNotContains(resp, "/details/")

    def test_send_message_fragment_has_no_visitor_bubble(self):
        """Optimistic UI: chat.html adds the visitor's bubble client-side the
        instant they submit, so the /send/ fragment must contain ONLY the bot
        reply — echoing the visitor bubble back would duplicate it."""
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Hi there"},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertNotIn("msg-row visitor", body)  # visitor bubble is client-side now
        self.assertIn("msg-row bot", body)         # bot reply still appended server-side
        # Both sides persisted as before.
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
        with ONE Conversation across visits, and skip the lead form on return."""
        vid = "v1s2t3u4v5w6x7y8"
        self.client.get(reverse("widget-chat"), {"site_key": "sk-test", "v": vid})
        self.client.post(
            reverse("widget-details", args=[vid]),
            {"site_key": "sk-test", "name": "Jane", "email": "jane@example.com"},
        )
        self.client.post(
            reverse("widget-send", args=[vid]),
            {"site_key": "sk-test", "message": "Hi again"},
        )

        # Second visit carrying the same v id but NO cookies at all (fresh
        # client simulates a cookie-blocking third-party iframe).
        cookieless = Client()
        resp = cookieless.get(reverse("widget-chat"), {"site_key": "sk-test", "v": vid})
        self.assertNotContains(resp, "/details/")  # returning visitor skips the form
        self.assertContains(resp, "Hi again")      # prior history still visible

        self.assertEqual(Customer.objects.filter(channel=self.channel).count(), 1)
        customer = Customer.objects.get(channel=self.channel)
        self.assertEqual(customer.email, "jane@example.com")
        self.assertEqual(customer.conversations.count(), 1)
        self.assertEqual(customer.conversations.get().messages.count(), 2)  # visitor + bot reply

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

        resp = self.client.post(
            reverse("widget-details", args=["!!!not-an-id!!!"]),
            {"site_key": "sk-test", "name": "Jane", "email": "jane@example.com"},
        )
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(Customer.objects.filter(channel=self.channel).exists())
