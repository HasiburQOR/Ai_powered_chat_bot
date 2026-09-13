"""Message timestamp invariants.

The widget poller returns bot bubbles with created_at STRICTLY greater than a
watermark handed out at send time. Wall clocks stall - Windows ticks only
every ~1-15ms - so two messages written in the same tick used to receive
IDENTICAL created_at values, and a bubble could then forever fail the poll's
created_at__gt filter (endless "typing..." with the reply already saved).
Message.save() guarantees strict per-conversation monotonicity instead.
"""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from conversations.models import Conversation, Customer, Message
from platforms.models import Channel

FROZEN = datetime(2026, 9, 13, 12, 0, 0, tzinfo=dt_timezone.utc)


class MessageTimestampTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.channel = Channel.objects.create(
            name="Web", channel_type="wordpress", is_active=True)

    def _conversation(self) -> Conversation:
        customer = Customer.objects.create(
            channel=self.channel, external_id="visitor-1")
        return Conversation.objects.create(
            customer=customer, last_message_at=timezone.now())

    def test_created_at_strictly_increases_even_when_the_clock_stalls(self):
        conversation = self._conversation()

        with patch("conversations.models.timezone.now", return_value=FROZEN):
            first = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.CUSTOMER, content="hi")
            second = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.BOT, content="hello!")

        self.assertEqual(first.created_at, FROZEN)
        self.assertEqual(second.created_at, FROZEN + timedelta(microseconds=1))
        self.assertLess(first.created_at, second.created_at)

    def test_many_messages_in_one_tick_keep_climbing(self):
        conversation = self._conversation()

        with patch("conversations.models.timezone.now", return_value=FROZEN):
            stamps = [
                Message.objects.create(
                    conversation=conversation,
                    sender_type=Message.SenderType.CUSTOMER,
                    content=f"m{i}").created_at
                for i in range(5)
            ]

        # All distinct and strictly increasing: a watermark equal to any one
        # of them can never swallow a later bubble.
        self.assertEqual(stamps, sorted(set(stamps)))
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertLess(earlier, later)

    def test_real_clock_moves_are_kept(self):
        """The bump only applies when the clock actually stalls - normal
        timestamps are whatever timezone.now() says."""
        conversation = self._conversation()

        real_now = timezone.now()
        with patch("conversations.models.timezone.now", return_value=real_now):
            first = Message.objects.create(
                conversation=conversation,
                sender_type=Message.SenderType.CUSTOMER, content="hi")
            later_time = real_now + timedelta(seconds=5)
            with patch("conversations.models.timezone.now", return_value=later_time):
                second = Message.objects.create(
                    conversation=conversation,
                    sender_type=Message.SenderType.BOT, content="hello!")

        self.assertEqual(first.created_at, real_now)
        self.assertEqual(second.created_at, later_time)

    def test_explicit_created_at_is_respected(self):
        """Manual/dashboard writes and test fixtures may pin their own
        timestamp - save() must not clobber it."""
        conversation = self._conversation()
        explicit = datetime(2026, 1, 1, 0, 0, 0, tzinfo=dt_timezone.utc)

        msg = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.SYSTEM, content="pinned",
            created_at=explicit)

        self.assertEqual(msg.created_at, explicit)
