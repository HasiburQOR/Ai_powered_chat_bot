import logging

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

logger = logging.getLogger(__name__)

# How many of the customer's own recent messages the extractor sees at once.
# Visitors answer the profile questions across several turns — sometimes as a
# single garbled burst ("Bangladesh: YEs have gcc , redence card expored date
# 28 y days, 6 people children under 5 2") — and extracting one message in
# isolation lost every detail it couldn't parse alone (seen live: BP-000009
# captured 2 of ~7 stated fields).
WINDOW_SIZE = 10


def _conversation_window_text(customer) -> str:
    """The customer's last WINDOW_SIZE inbound messages, oldest first."""
    from conversations.models import Message

    recent = list(
        Message.objects.filter(
            conversation__customer=customer,
            sender_type=Message.SenderType.CUSTOMER,
        )
        .order_by("-created_at")
        .values_list("content", flat=True)[:WINDOW_SIZE]
    )
    recent.reverse()
    return "\n".join(line.strip() for line in recent if line and line.strip())


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

    fields = extract_fields(extraction_text)
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