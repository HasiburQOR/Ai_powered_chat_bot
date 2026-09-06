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
