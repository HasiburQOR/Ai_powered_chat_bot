"""Meta channel sends: the bot's **bold** key facts must degrade to plain
text (Messenger/Instagram render no markup — the markers would reach
visitors as literal asterisks) and convert to WhatsApp's native *bold*.
Long WhatsApp replies are split into <=4096-char bubbles."""
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from platforms.models import Channel
from platforms.send import (
    INSTAGRAM_LOGIN_MESSAGES_URL,
    WHATSAPP_MESSAGES_URL,
    _chunk_text,
    _plain_text,
    _wa_text,
    send_platform_reply,
)


class PlainTextTests(SimpleTestCase):
    def test_paired_markers_stripped_words_kept(self):
        self.assertEqual(
            _plain_text("Got it - **Baku, 12-16 Dec** for 4 adults"),
            "Got it - Baku, 12-16 Dec for 4 adults")

    def test_multiple_pairs_and_stray_markers(self):
        self.assertEqual(_plain_text("**A** and **B** ** C"), "A and B  C")

    def test_empty_and_none(self):
        self.assertEqual(_plain_text(""), "")
        self.assertEqual(_plain_text(None), "")


class WhatsAppTextTests(SimpleTestCase):
    """WhatsApp renders single-asterisk bold — the bot's **bold** converts
    instead of being stripped like on Messenger/Instagram."""

    def test_paired_markers_become_whatsapp_bold(self):
        self.assertEqual(
            _wa_text("Got it - **Baku, 12-16 Dec** for 4 adults"),
            "Got it - *Baku, 12-16 Dec* for 4 adults")

    def test_multiple_pairs_and_stray_markers(self):
        self.assertEqual(_wa_text("**A** and **B** ** C"), "*A* and *B*  C")

    def test_empty_and_none(self):
        self.assertEqual(_wa_text(""), "")
        self.assertEqual(_wa_text(None), "")


class ChunkTextTests(SimpleTestCase):
    def test_short_text_is_a_single_chunk(self):
        self.assertEqual(list(_chunk_text("hello", limit=10)), ["hello"])

    def test_splits_on_newlines_under_the_limit(self):
        self.assertEqual(
            list(_chunk_text("aaa\nbbb\nccc", limit=7)),
            ["aaa\n", "bbb\nccc"])

    def test_single_oversized_line_hard_split(self):
        self.assertEqual(
            [len(c) for c in _chunk_text("x" * 25, limit=10)],
            [10, 10, 5])

    def test_reassembly_preserves_content(self):
        text = "\n".join(f"para {i} " + "x" * 900 for i in range(12))
        chunks = list(_chunk_text(text))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 4096 for c in chunks))
        self.assertEqual("".join(chunks), text)


class WhatsAppSendTests(TestCase):
    def _channel(self, credentials=None):
        return Channel.objects.create(
            name="WA Line", channel_type="whatsapp", is_active=True,
            credentials=credentials if credentials is not None else {
                "phone_number_id": "PNID1", "access_token": "tok"})

    @patch("platforms.send.requests.post")
    def test_reply_sent_via_cloud_api(self, mock_post):
        mock_post.return_value.ok = True
        ok = send_platform_reply(self._channel(), "8801712345678", "**Baku**, 5 days")
        self.assertTrue(ok)
        self.assertEqual(
            mock_post.call_args.args[0],
            WHATSAPP_MESSAGES_URL.format(phone_number_id="PNID1"))
        headers = mock_post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer tok")
        body = mock_post.call_args.kwargs["json"]
        self.assertEqual(body["messaging_product"], "whatsapp")
        self.assertEqual(body["to"], "8801712345678")
        self.assertEqual(body["type"], "text")
        self.assertEqual(body["text"]["body"], "*Baku*, 5 days")

    @patch("platforms.send.requests.post")
    def test_long_reply_is_chunked_into_bubbles(self, mock_post):
        mock_post.return_value.ok = True
        text = "\n".join("para " + "x" * 1500 for _ in range(6))  # > 4096 chars
        ok = send_platform_reply(self._channel(), "8801712345678", text)
        self.assertTrue(ok)
        bodies = [c.kwargs["json"]["text"]["body"] for c in mock_post.call_args_list]
        self.assertGreater(len(bodies), 1)
        self.assertTrue(all(len(b) <= 4096 for b in bodies))
        self.assertEqual("".join(bodies), text)

    @patch("platforms.send.requests.post")
    def test_missing_credentials_short_circuit(self, mock_post):
        ok = send_platform_reply(self._channel(credentials={}), "8801712345678", "hi")
        self.assertFalse(ok)
        mock_post.assert_not_called()

    @patch("platforms.send.requests.post")
    def test_failed_send_reports_failure(self, mock_post):
        mock_post.return_value.ok = False
        mock_post.return_value.status_code = 400
        mock_post.return_value.text = '{"error":{"code":131047}}'
        ok = send_platform_reply(self._channel(), "8801712345678", "hi")
        self.assertFalse(ok)


class InstagramLoginSendTests(TestCase):
    """Channels set up via "API setup with Instagram login" hold an Instagram
    user token as access_token and must send through graph.instagram.com."""

    @patch("platforms.send.requests.post")
    def test_reply_sent_via_instagram_graph(self, mock_post):
        mock_post.return_value.ok = True
        channel = Channel.objects.create(
            name="IG", channel_type="instagram",
            credentials={"page_id": "17841400000000000", "access_token": "IGAAtok"},
        )
        self.assertTrue(send_platform_reply(channel, "igsid-1", "Hi **there**"))
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], INSTAGRAM_LOGIN_MESSAGES_URL)
        self.assertEqual(mock_post.call_args[1]["headers"]["Authorization"], "Bearer IGAAtok")
        self.assertEqual(mock_post.call_args[1]["json"],
                         {"recipient": {"id": "igsid-1"}, "message": {"text": "Hi there"}})

    @patch("platforms.send.requests.post")
    def test_page_token_channel_keeps_facebook_graph(self, mock_post):
        mock_post.return_value.ok = True
        channel = Channel.objects.create(
            name="IG-FB", channel_type="instagram",
            credentials={"page_id": "1", "page_access_token": "EAAtok"},
        )
        send_platform_reply(channel, "igsid-1", "Hi")
        self.assertIn("graph.facebook.com", mock_post.call_args[0][0])

    @patch("platforms.send.requests.post")
    def test_long_reply_split_at_instagram_limit(self, mock_post):
        mock_post.return_value.ok = True
        channel = Channel.objects.create(
            name="IG", channel_type="instagram",
            credentials={"page_id": "1", "access_token": "IGAAtok"},
        )
        send_platform_reply(channel, "igsid-1", ("x" * 900 + "\n") * 3)
        self.assertEqual(mock_post.call_count, 3)
        for call in mock_post.call_args_list:
            self.assertLessEqual(len(call[1]["json"]["message"]["text"]), 1000)
