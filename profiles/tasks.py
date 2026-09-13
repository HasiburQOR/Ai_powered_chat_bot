import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

logger = logging.getLogger(__name__)

# How many recent messages (BOTH sides) the extractor sees at once, as a
# labelled transcript. Visitors answer the profile questions across several
# turns — sometimes as a single garbled burst ("Bangladesh: YEs have gcc ,
# redence card expored date 28 y days, 6 people children under 5 2") — and
# extracting one message in isolation lost every detail it couldn't parse
# alone (seen live: BP-000009 captured 2 of ~7 stated fields). Including the
# bot's lines labels each answer with the question it belongs to: a bare
# "01712345678" is uninterpretable alone, obvious right after
# "[Bot] ... WhatsApp number?".
WINDOW_SIZE = 14


def _conversation_window_text(customer) -> str:
    """A labelled transcript of the customer's last WINDOW_SIZE messages,
    oldest first: one "[Bot] ..." / "[Customer] ..." line per message."""
    from conversations.models import Message

    recent = list(
        Message.objects.filter(conversation__customer=customer)
        .order_by("-created_at")
        .values_list("sender_type", "content")[:WINDOW_SIZE]
    )
    recent.reverse()
    lines = []
    for sender_type, content in recent:
        content = (content or "").strip()
        if not content:
            continue
        who = "Bot" if sender_type == Message.SenderType.BOT else "Customer"
        lines.append(f"[{who}] {content}")
    return "\n".join(lines)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def extract_profile_task(self, customer_id: str, text: str = ""):
    """Fire-and-forget from the engine: pull travel details out of the
    visitor's own words and merge them into their TravelProfile.

    Extraction runs over the customer's recent messages (not just the current
    one) so details split across turns still land — apply_fields merges
    per-field, so re-extracting already-known values is harmless. A transient
    LLM failure (timeout, or a reasoning model answering with empty content —
    seen live with glm-5.3-flash: 20s of thinking, then "") is retried on the
    declared ladder instead of being dropped silently. Giving up is logged.
    """
    from conversations.models import Customer

    from .extraction import apply_fields, extract_fields

    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        return
    extraction_text = _conversation_window_text(customer) or (text or "").strip()
    if not extraction_text:
        return

    profile = getattr(customer, "travel_profile", None)
    missing = profile.missing_fields() if profile else None
    fields = extract_fields(extraction_text, missing_fields=missing)
    if fields is None:
        try:
            raise self.retry(countdown=self.default_retry_delay)
        except MaxRetriesExceededError:
            logger.warning(
                "Travel-profile extraction for customer %s gave up after %d "
                "attempts — the LLM kept failing; details in this window were "
                "not captured.", customer_id, self.max_retries + 1)
            return
    if not fields:
        return
    profile = apply_fields(customer, fields)
    logger.info("Travel profile %s updated from message text (%d field(s)): %s",
                profile.profile_number, len(fields), ", ".join(fields))