from django.test import TestCase
from django.urls import reverse

from conversations.models import Customer
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

        # A customer row exists but has no contact details yet.
        customer = Customer.objects.get(channel=self.channel)
        self.assertEqual(customer.email, "")
        self.assertFalse(customer.has_contact_details)

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

    def test_send_message_still_works(self):
        self._visit()
        resp = self.client.post(
            reverse("widget-send", args=[self._session_id()]),
            {"site_key": "sk-test", "message": "Hi there"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hi there")
