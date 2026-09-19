import hashlib
import hmac
import json
from unittest.mock import patch

from django.test import TestCase, override_settings

from platforms.models import Channel


def make_payload(sender_id="12345", text="hello", obj="page", page_id="999"):
    return json.dumps({
        "object": obj,
        "entry": [{
            "id": page_id,
            "messaging": [{
                "sender": {"id": sender_id},
                "message": {"text": text},
            }],
        }],
    }).encode()


class MetaWebhookTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Test Page",
            channel_type="messenger",
            is_active=True,
            credentials={
                "page_id": "999",
                "app_secret": "shhh-secret",
                "verify_token": "verify-me",
                "page_access_token": "tok",
            },
        )

    # --- GET verification ---

    def test_verify_success(self):
        resp = self.client.get("/webhooks/meta/", {"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "ch4ll3ng3"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"ch4ll3ng3")

    def test_verify_bad_token(self):
        resp = self.client.get("/webhooks/meta/", {"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"})
        self.assertEqual(resp.status_code, 403)

    def test_verify_bad_mode(self):
        resp = self.client.get("/webhooks/meta/", {"hub.mode": "other", "hub.verify_token": "verify-me", "hub.challenge": "x"})
        self.assertEqual(resp.status_code, 403)

    # --- POST signature verification ---

    def _post_signed(self, payload: bytes, secret="shhh-secret"):
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return self.client.post(
            "/webhooks/meta/", data=payload, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sig,
        )

    @patch("webhooks.views.process_inbound_message")
    def test_valid_signature_enqueues_task(self, mock_task):
        payload = make_payload()
        resp = self._post_signed(payload)
        self.assertEqual(resp.status_code, 200)
        mock_task.delay.assert_called_once()
        args = mock_task.delay.call_args[0]
        self.assertEqual(args[0], "messenger")
        self.assertEqual(args[1], "999")
        self.assertEqual(args[2], "12345")
        self.assertEqual(args[3], "hello")

    @patch("webhooks.views.process_inbound_message")
    def test_invalid_signature_rejected(self, mock_task):
        payload = make_payload()
        bad = "sha256=" + "0" * 64
        resp = self.client.post(
            "/webhooks/meta/", data=payload, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=bad,
        )
        self.assertEqual(resp.status_code, 403)
        mock_task.delay.assert_not_called()

    @patch("webhooks.views.process_inbound_message")
    def test_missing_signature_rejected(self, mock_task):
        resp = self.client.post("/webhooks/meta/", data=make_payload(), content_type="application/json")
        self.assertEqual(resp.status_code, 403)
        mock_task.delay.assert_not_called()

    @patch("webhooks.views.process_inbound_message")
    def test_instagram_object_tags_channel(self, mock_task):
        payload = make_payload(obj="instagram")
        resp = self._post_signed(payload)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(mock_task.delay.call_args[0][0], "instagram")

    def test_get_without_hub_params_logs_rejection(self):
        """Regression: bare GETs (bot/scanner probes) used to 403 with NO log
        line, making stray 'django.request: Forbidden' entries impossible to
        attribute — they looked exactly like a Meta rejection."""
        with self.assertLogs("webhooks.views", level="WARNING") as logs:
            resp = self.client.get("/webhooks/meta/")
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(any("bad verification request" in line for line in logs.output))

    @patch("webhooks.views.process_inbound_message")
    def test_accepted_post_logs_info_line(self, mock_task):
        """Acceptance must be visible: a silent 200 made it impossible to tell
        whether Meta's traffic was arriving at all."""
        with self.assertLogs("webhooks.views", level="INFO") as logs:
            resp = self._post_signed(make_payload())
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(any("Meta webhook accepted" in line for line in logs.output))


def make_whatsapp_payload(sender_wa="8801712345678", text="hi there",
                          phone_number_id="PNID1", profile_name="Ravi Kumar"):
    """WhatsApp Cloud API webhook shape: entry[].changes[].value.messages[]."""
    return json.dumps({
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "WABA1",
            "changes": [{
                "field": "messages",
                "value": {
                    "messaging_product": "whatsapp",
                    "metadata": {
                        "display_phone_number": "8809611000111",
                        "phone_number_id": phone_number_id,
                    },
                    "contacts": [{"profile": {"name": profile_name}, "wa_id": sender_wa}],
                    "messages": [{
                        "from": sender_wa,
                        "id": "wamid.HBgNODgwMTcxMjM0NTY3ODc",
                        "timestamp": "1757942400",
                        "text": {"body": text},
                        "type": "text",
                    }],
                },
            }],
        }],
    }).encode()


class WhatsAppWebhookTests(TestCase):
    """WhatsApp Cloud API webhooks arrive at the SAME /webhooks/meta/
    endpoint with object=whatsapp_business_account and a different entry
    shape; the channel is identified by metadata.phone_number_id."""

    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="WhatsApp Line",
            channel_type="whatsapp",
            is_active=True,
            credentials={
                "phone_number_id": "PNID1",
                "access_token": "tok",
                "app_secret": "wa-secret",
                "verify_token": "wa-verify",
            },
        )

    def test_verify_success_with_whatsapp_channel(self):
        resp = self.client.get("/webhooks/meta/", {
            "hub.mode": "subscribe", "hub.verify_token": "wa-verify",
            "hub.challenge": "ch4ll3ng3"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"ch4ll3ng3")

    def _post_signed(self, payload: bytes, secret="wa-secret"):
        sig = "sha256=" + hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
        return self.client.post(
            "/webhooks/meta/", data=payload, content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=sig,
        )

    @patch("webhooks.views.process_inbound_message")
    def test_valid_signature_enqueues_whatsapp_task(self, mock_task):
        resp = self._post_signed(make_whatsapp_payload())
        self.assertEqual(resp.status_code, 200)
        mock_task.delay.assert_called_once()
        args, kwargs = mock_task.delay.call_args
        self.assertEqual(args[0], "whatsapp")
        self.assertEqual(args[1], "PNID1")            # phone_number_id identifies the channel
        self.assertEqual(args[2], "8801712345678")    # customer's wa_id
        self.assertEqual(args[3], "hi there")
        self.assertEqual(kwargs["profile_name"], "Ravi Kumar")
        self.assertEqual(kwargs["raw_payload"]["id"], "wamid.HBgNODgwMTcxMjM0NTY3ODc")

    @patch("webhooks.views.process_inbound_message")
    def test_wrong_signature_rejected(self, mock_task):
        resp = self._post_signed(make_whatsapp_payload(), secret="wrong-secret")
        self.assertEqual(resp.status_code, 403)
        mock_task.delay.assert_not_called()

    @patch("webhooks.views.process_inbound_message")
    def test_non_text_messages_acked_and_ignored(self, mock_task):
        payload = json.loads(make_whatsapp_payload())
        payload["entry"][0]["changes"][0]["value"]["messages"] = [
            {"from": "8801712345678", "type": "image", "image": {"id": "M1"}},
            {"from": "8801712345678", "type": "audio", "audio": {"id": "M2"}},
        ]
        resp = self._post_signed(json.dumps(payload).encode())
        self.assertEqual(resp.status_code, 200)
        mock_task.delay.assert_not_called()

    @patch("webhooks.views.process_inbound_message")
    def test_whitespace_only_text_skipped(self, mock_task):
        payload = json.loads(make_whatsapp_payload(text="   "))
        resp = self._post_signed(json.dumps(payload).encode())
        self.assertEqual(resp.status_code, 200)
        mock_task.delay.assert_not_called()

    def _swap_credentials(self, **new_values):
        """Update the stored credentials via QuerySet.update so the class-level
        cached instance from setUpTestData stays untouched for later tests."""
        merged = dict(self.channel.credentials, **new_values)
        Channel.objects.filter(pk=self.channel.pk).update(credentials=merged)

    def test_padded_stored_app_secret_still_validates(self):
        """Regression: an app_secret pasted with trailing whitespace (classic
        copy-paste artifact) must still validate — the view strips it."""
        self._swap_credentials(app_secret="wa-secret \n")
        with patch("webhooks.views.process_inbound_message") as mock_task:
            resp = self._post_signed(make_whatsapp_payload(), secret="wa-secret")
        self.assertEqual(resp.status_code, 200)
        mock_task.delay.assert_called_once()

    def test_padded_stored_verify_token_still_verifies_get(self):
        """Regression: a stored verify_token with a trailing newline must still
        satisfy the GET verification handshake."""
        self._swap_credentials(verify_token="wa-verify\n")
        resp = self.client.get("/webhooks/meta/", {
            "hub.mode": "subscribe", "hub.verify_token": "wa-verify",
            "hub.challenge": "ch4ll3ng3"})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, b"ch4ll3ng3")

    def test_missing_app_secret_key_rejected(self):
        """Documents the classic misconfiguration: verify_token present (so GET
        verification succeeds) but app_secret missing/mis-keyed → POST 403."""
        broken = {k: v for k, v in self.channel.credentials.items() if k != "app_secret"}
        Channel.objects.filter(pk=self.channel.pk).update(credentials=broken)
        with patch("webhooks.views.process_inbound_message") as mock_task:
            resp = self._post_signed(make_whatsapp_payload(), secret="wa-secret")
        self.assertEqual(resp.status_code, 403)
        mock_task.delay.assert_not_called()
