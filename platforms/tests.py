"""Meta channel sends: the bot's **bold** key facts must degrade to plain
text (Messenger/Instagram render no markup — the markers would reach
visitors as literal asterisks)."""
from django.test import SimpleTestCase

from platforms.send import _plain_text


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
