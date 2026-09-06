import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_inbound_message(self, channel_type, page_id, sender_id, text, raw_payload=None):
    """Webhook entrypoint: resolve channel/customer/conversation, run the engine,
    send the reply via the platform's Graph API."""
    from bot.engine import handle_inbound_message
    from conversations.models import Conversation, Customer
    from django.utils import timezone
    from platforms.models import Channel
    from platforms.send import send_platform_reply

    # credentials are encrypted at rest, so matching on page_id must happen in Python.
    channel = None
    for c in Channel.objects.filter(channel_type=channel_type, is_active=True):
        if str((c.credentials or {}).get("page_id") or "") == str(page_id):
            channel = c
            break
    if channel is None:
        known = [
            (c.credentials or {}).get("page_id")
            for c in Channel.objects.filter(channel_type=channel_type, is_active=True)
        ]
        logger.warning(
            "Dropping inbound message: no active %s channel with page_id=%r (known: %r) "
            "from sender=%r text=%r",
            channel_type, str(page_id), known, sender_id, (text or "")[:100],
        )
        return  # Unknown/deactivated channel — ignore.

    customer, _ = Customer.objects.get_or_create(
        channel=channel,
        external_id=sender_id,
        defaults={"display_name": ""},
    )
    conversation = customer.conversations.order_by("-last_message_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(
            customer=customer, last_message_at=timezone.now()
        )

    reply = handle_inbound_message(conversation, text, raw_payload=raw_payload)
    send_platform_reply(channel, sender_id, reply.content)


@shared_task
def summarize_customer_memory(customer_id):
    """Feed the recent transcript to the LLM and overwrite Customer.memory_summary
    with a short durable-facts summary."""
    from conversations.models import Customer, Message
    from llm.adapters import get_adapter
    from llm.models import LLMConfig

    config = LLMConfig.objects.filter(is_active=True).first()
    if config is None:
        return
    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return

    transcript = "\n".join(
        f"{'Bot' if m.sender_type == Message.SenderType.BOT else 'Customer'}: {m.content}"
        for c in customer.conversations.all()
        for m in c.messages.order_by("created_at")
    )
    if not transcript.strip():
        return

    messages = [
        {
            "role": "system",
            "content": (
                "Summarize the durable facts worth remembering about this customer "
                "(name, preferences, order issues, commitments made, etc.) in a short "
                "paragraph. Ignore small talk. Output only the summary."
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
        return
    if summary and summary.strip():
        Customer.objects.filter(pk=customer_id).update(memory_summary=summary.strip()[:10000])
