import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=30)
def extract_profile_task(self, customer_id: str, text: str):
    """Fire-and-forget from the engine: pull travel details out of the
    visitor's own words and merge them into their TravelProfile."""
    from conversations.models import Customer

    from .extraction import apply_fields, extract_fields

    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None or not (text or "").strip():
        return
    fields = extract_fields(text)
    if not fields:
        return
    profile = apply_fields(customer, fields)
    logger.info("Travel profile %s updated from message text (%d field(s)): %s",
                profile.profile_number, len(fields), ", ".join(fields))