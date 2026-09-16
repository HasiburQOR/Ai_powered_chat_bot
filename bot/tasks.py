import logging
from datetime import timedelta

from celery import shared_task
from django.db.models import F, Max, Q
from django.utils import timezone

logger = logging.getLogger(__name__)

# Saved as an extra bot bubble if the widget pipeline blows up, so the
# visitor's poller always receives SOMETHING and never stares at dots forever.
WIDGET_ERROR_TEXT = "Sorry, something went wrong on our side. Please try again in a moment."


# The credential key that identifies which account on a channel type
# received a message: page_id for the Meta messaging surfaces (Messenger /
# Instagram), phone_number_id for the WhatsApp Cloud API.
CHANNEL_IDENTITY_KEYS = {
    "instagram": "page_id",
    "messenger": "page_id",
    "whatsapp": "phone_number_id",
}


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_inbound_message(self, channel_type, page_id, sender_id, text,
                            raw_payload=None, profile_name=""):
    """Webhook entrypoint: resolve channel/customer/conversation, run the engine,
    send the reply via the platform's Graph API. `page_id` is the channel's
    account identifier — page_id for Messenger/Instagram, phone_number_id for
    WhatsApp. `profile_name` (WhatsApp contacts[0].profile.name) labels the
    Customer when available."""
    from bot.engine import handle_inbound_message
    from conversations.models import Conversation, Customer
    from django.utils import timezone
    from platforms.models import Channel
    from platforms.send import send_platform_reply

    # credentials are encrypted at rest, so matching on the identity key must
    # happen in Python.
    identity_key = CHANNEL_IDENTITY_KEYS.get(channel_type, "page_id")
    channel = None
    for c in Channel.objects.filter(channel_type=channel_type, is_active=True):
        if str((c.credentials or {}).get(identity_key) or "") == str(page_id):
            channel = c
            break
    if channel is None:
        known = [
            (c.credentials or {}).get(identity_key)
            for c in Channel.objects.filter(channel_type=channel_type, is_active=True)
        ]
        logger.warning(
            "Dropping inbound message: no active %s channel with %s=%r (known: %r) "
            "from sender=%r text=%r",
            channel_type, identity_key, str(page_id), known, sender_id, (text or "")[:100],
        )
        return  # Unknown/deactivated channel — ignore.

    customer, _ = Customer.objects.get_or_create(
        channel=channel,
        external_id=sender_id,
        defaults={"display_name": profile_name or ""},
    )
    # Backfill the display name for customers created before WhatsApp started
    # sending profile names (or that chatted in nameless first).
    if profile_name and not customer.display_name:
        customer.display_name = profile_name
        customer.save(update_fields=["display_name"])
    conversation = customer.conversations.order_by("-last_message_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(
            customer=customer, last_message_at=timezone.now()
        )

    replies = handle_inbound_message(conversation, text, raw_payload=raw_payload)
    for reply in replies:
        send_platform_reply(channel, sender_id, reply.content)


@shared_task
def process_widget_message(conversation_id, text):
    """Widget entrypoint: the same pipeline as the Meta path (rules → retrieval
    → LLM), minus the platform-send step — replies are stored as Messages and
    the widget picks them up by polling. The HTTP view only enqueues this, so
    a burst of concurrent visitors can never exhaust gunicorn's sync workers;
    each LLM call happens on a Celery worker thread instead."""
    from bot.engine import handle_inbound_message
    from conversations.models import Conversation, Message

    conversation = Conversation.objects.filter(pk=conversation_id).first()
    if conversation is None:
        logger.warning("process_widget_message: conversation %s vanished before the task ran", conversation_id)
        return
    try:
        handle_inbound_message(conversation, text)
    except Exception:
        logger.exception("Widget message processing failed for conversation %s", conversation_id)
        # Never leave the visitor hanging: the poller returns whatever bot
        # bubbles exist for the turn, so persist an apology bubble.
        Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=WIDGET_ERROR_TEXT,
        )


@shared_task
def summarize_customer_memory(customer_id):
    """Feed the recent transcript to the LLM and overwrite Customer.memory_summary
    with a short durable-facts summary."""
    from conversations.models import Customer

    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return
    _summarize_customer(customer)


def _summarize_customer(customer) -> bool:
    """Shared summarizer used by BOTH trigger paths — the message-count trigger
    and the beat-driven idle summarizer — so their output and bookkeeping
    (including the memory_summary_at stamp) can never drift apart.
    Returns True when a fresh summary was stored."""
    from conversations.models import Customer, Message
    from llm.adapters import get_adapter
    from llm.models import LLMConfig

    config = LLMConfig.objects.filter(is_active=True).first()
    if config is None:
        return False

    transcript = "\n".join(
        f"{'Bot' if m.sender_type == Message.SenderType.BOT else 'Customer'}: {m.content}"
        for c in customer.conversations.all()
        for m in c.messages.order_by("created_at")
    )
    if not transcript.strip():
        return False

    messages = [
        {
            "role": "system",
            "content": (
                "You maintain the long-term memory for a travel-agency chatbot. "
                "The transcript lines are labelled 'Bot:' or 'Customer:'. "
                "Summarize the DURABLE facts worth remembering about this "
                "customer for future conversations, as a compact list with one "
                "fact per line. Capture, when stated: destinations and trip "
                "purpose; travel dates, trip length and flexibility; party "
                "size (adults/children with ages); budget; package, hotel and "
                "airline preferences; visa/residence constraints (nationality, "
                "GCC residence card expiry); name and WhatsApp number; "
                "commitments the bot made (prices quoted, follow-ups "
                "promised). Prefer the customer's own wording for proper "
                "nouns. Take facts from what the CUSTOMER said or confirmed — "
                "never invent, never restate the bot's unanswered questions. "
                "Ignore small talk and greetings. Output only the list, no "
                "preamble."
            ),
        },
        {
            "role": "user",
            "content": (f"Existing summary:\n{customer.memory_summary or '(none)'}\n\nTranscript:\n{transcript}"),
        },
    ]
    try:
        summary = get_adapter(config).send(messages, config)
    except Exception:
        logger.warning("Memory summarization LLM call failed for customer %s", customer.pk, exc_info=True)
        return False
    if not (summary and summary.strip()):
        return False
    Customer.objects.filter(pk=customer.pk).update(
        memory_summary=summary.strip()[:10000],
        memory_summary_at=timezone.now(),
    )
    return True


@shared_task
def summarize_idle_customers():
    """Beat task (every 10 min): customers whose latest activity falls in the
    30-min..48h window but whose summary is missing or older than that
    activity. Keeps memory fresh for visitors who idle mid-conversation (the
    message-count trigger only fires while they are actively chatting) and for
    return visits, without re-summarizing chatters who are still going."""
    from conversations.models import Customer

    window_start = timezone.now() - timedelta(hours=48)
    window_end = timezone.now() - timedelta(minutes=30)
    stale = (
        Customer.objects
        .annotate(last_activity=Max("conversations__last_message_at"))
        .filter(last_activity__gte=window_start, last_activity__lte=window_end)
        .filter(
            Q(memory_summary_at__isnull=True)
            | Q(memory_summary_at__lt=F("last_activity"))
        )
    )
    for customer in stale:
        summarize_customer_memory.delay(str(customer.pk))
