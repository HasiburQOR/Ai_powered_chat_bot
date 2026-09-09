import csv
from io import StringIO

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from conversations.models import Conversation, Customer, Message
from platforms.models import Channel


class ChannelCrudTests(TestCase):
    """Channel management: OOB refresh fix, deactivate (soft) and delete (hard)."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            username="staff", password="pw", is_staff=True
        )

    def setUp(self):
        self.client.force_login(self.staff)

    @staticmethod
    def _make_channel(name="Main IG", **kwargs):
        return Channel.objects.create(name=name, channel_type="instagram", **kwargs)

    def test_channel_create_returns_oob_tbody_fragment(self):
        # Regression test: the response used to START with a <script> tag.
        # htmx 1.9.12 parses fragments whose first tag is <script> inside a
        # plain <div>, where the HTML parser drops <tbody>/<tr>/<td> tags —
        # the hx-swap-oob attribute vanished, the table never updated and the
        # mangled rows were dumped into the modal ("CSS broke after adding").
        resp = self.client.post(reverse("dashboard-channel-create"), {
            "name": "WordPress Blog", "channel_type": "wordpress",
            "is_active": "on", "credentials": "",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertTrue(body.lstrip().startswith("<tbody"), body[:120])
        self.assertNotIn("<script>", body)
        self.assertIn('id="channel-tbody"', body)
        self.assertIn('hx-swap-oob="innerHTML"', body)
        self.assertIn("WordPress Blog", body)
        self.assertTrue(Channel.objects.filter(name="WordPress Blog").exists())

    def test_channel_update_returns_oob_tbody_fragment(self):
        channel = self._make_channel()
        resp = self.client.post(reverse("dashboard-channel-edit", args=[channel.pk]), {
            "name": "Renamed IG", "channel_type": "instagram",
            "is_active": "on", "credentials": "",
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.content.decode()
        self.assertTrue(body.lstrip().startswith("<tbody"), body[:120])
        self.assertNotIn("<script>", body)
        self.assertIn("Renamed IG", body)

    def test_channel_delete_is_hard_delete_and_cascades(self):
        channel = self._make_channel()
        customer = Customer.objects.create(
            channel=channel, external_id="ig-1", display_name="Visitor",
            email="visitor@example.com",
        )
        conversation = Conversation.objects.create(customer=customer, last_message_at=timezone.now())
        Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.CUSTOMER,
            content="hello",
        )

        resp = self.client.post(reverse("dashboard-channel-delete", args=[channel.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"")  # empty body -> HTMX removes the row
        self.assertFalse(Channel.objects.filter(pk=channel.pk).exists())
        self.assertFalse(Customer.objects.filter(pk=customer.pk).exists())
        self.assertFalse(Conversation.objects.filter(pk=conversation.pk).exists())

    def test_channel_deactivate_is_soft_delete(self):
        channel = self._make_channel(is_active=True)
        resp = self.client.post(reverse("dashboard-channel-deactivate", args=[channel.pk]))
        self.assertEqual(resp.status_code, 200)
        channel.refresh_from_db()
        self.assertFalse(channel.is_active)  # deactivated, history preserved

    def test_channel_endpoints_require_staff(self):
        self.client.logout()
        resp = self.client.post(
            reverse("dashboard-channel-delete", args=["00000000-0000-0000-0000-000000000000"])
        )
        self.assertEqual(resp.status_code, 302)  # redirected to the login page


class ConversationFilterExportTests(TestCase):
    """Visitor-details search filters and the CSV download."""

    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_user(
            username="staff", password="pw", is_staff=True
        )
        cls.ig = Channel.objects.create(name="Instagram", channel_type="instagram", is_active=True)
        cls.wp = Channel.objects.create(name="Website", channel_type="wordpress", is_active=True)

        alice = Customer.objects.create(
            channel=cls.ig, external_id="ig-1", display_name="Alice",
            email="alice@example.com", phone="+15550001",
        )
        bob = Customer.objects.create(
            channel=cls.wp, external_id="wp-1", display_name="Bob",  # no contact details
        )
        cls.alice_conversation = Conversation.objects.create(customer=alice, last_message_at=timezone.now())
        Conversation.objects.create(customer=bob, last_message_at=timezone.now())
        Message.objects.create(
            conversation=cls.alice_conversation,
            sender_type=Message.SenderType.CUSTOMER,
            content="Where is my order?",
        )
        Message.objects.create(
            conversation=cls.alice_conversation,
            sender_type=Message.SenderType.BOT,
            content="Checking now!",
        )

    def setUp(self):
        self.client.force_login(self.staff)

    @staticmethod
    def _rows(resp):
        return list(csv.reader(StringIO(resp.content.decode())))

    def test_export_includes_details_and_transcript(self):
        resp = self.client.get(reverse("dashboard-conversation-export"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn("attachment", resp["Content-Disposition"])

        rows = self._rows(resp)
        self.assertEqual(rows[0][:4], ["Conversation ID", "Visitor Name", "Email", "Phone"])
        by_name = {row[1]: row for row in rows[1:]}
        self.assertIn("Alice", by_name)
        self.assertIn("Bob", by_name)

        alice_row = by_name["Alice"]
        self.assertEqual(alice_row[2], "alice@example.com")
        self.assertEqual(alice_row[3], "+15550001")
        self.assertEqual(alice_row[5], "Instagram")
        self.assertEqual(alice_row[6], "Bot")  # default conversation status
        self.assertEqual(alice_row[10], "2")
        self.assertIn("Where is my order?", alice_row[11])
        self.assertIn("Checking now!", alice_row[11])

    def test_export_respects_search_and_details_filters(self):
        resp = self.client.get(reverse("dashboard-conversation-export"), {"q": "alice@example.com"})
        self.assertEqual([row[1] for row in self._rows(resp)[1:]], ["Alice"])

        resp = self.client.get(reverse("dashboard-conversation-export"), {"has_details": "yes"})
        self.assertEqual([row[1] for row in self._rows(resp)[1:]], ["Alice"])

        resp = self.client.get(reverse("dashboard-conversation-export"), {"has_details": "no"})
        self.assertEqual([row[1] for row in self._rows(resp)[1:]], ["Bob"])

    def test_export_respects_channel_and_status_filters(self):
        resp = self.client.get(reverse("dashboard-conversation-export"), {"channel": str(self.ig.pk)})
        self.assertEqual([row[1] for row in self._rows(resp)[1:]], ["Alice"])

        resp = self.client.get(reverse("dashboard-conversation-export"), {"status": "human"})
        self.assertEqual([row[1] for row in self._rows(resp)[1:]], [])

    def test_list_search_filters_by_visitor_details(self):
        resp = self.client.get(reverse("dashboard-conversations"), {"q": "Alice"})
        self.assertContains(resp, "Alice")
        self.assertNotContains(resp, "Bob")

        resp = self.client.get(reverse("dashboard-conversations"), {"has_details": "yes"})
        self.assertContains(resp, "alice@example.com")
        self.assertNotContains(resp, "Bob")

    def test_export_escapes_formula_injection(self):
        attacker = Customer.objects.create(
            channel=self.wp, external_id="wp-2", display_name="=cmd|'/c calc'!A0",
        )
        Conversation.objects.create(customer=attacker, last_message_at=timezone.now())

        resp = self.client.get(reverse("dashboard-conversation-export"))
        self.assertIn("'=cmd", resp.content.decode())  # neutralized with a leading apostrophe


