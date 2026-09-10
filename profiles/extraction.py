"""LLM-driven extraction of travel-profile details from free visitor text."""
import datetime as dt
import json
import logging

from django.utils import timezone

from llm.adapters import get_adapter
from llm.models import LLMConfig

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You read a chat message from a travel-agency customer and extract trip-lead details.
Return a single JSON object. Include ONLY keys you actually found in the text:
  full_name (string), whatsapp_number (string), nationality (string),
  residence_country (string), gcc_residence_card (true/false),
  residence_card_expiry (YYYY-MM-DD), travel_date (YYYY-MM-DD),
  trip_days (integer), adults (integer), children_ages (string like "5, 8"),
  travel_intent (true/false: does the message express interest in a trip).
Rules:
- Fuzzy dates ("next month", "12 Oct", "in 3 weeks") become a best-effort YYYY-MM-DD using today's date; if impossible, omit the key.
- travel_intent is true whenever the visitor asks about destinations, packages,
  prices, visas, flights, hotels or any trip planning — in ANY language.
- Never guess. Omit anything the customer did not state or clearly imply.
- Output ONLY the JSON object — no commentary, no code fences."""

_DATE_FORMATS = (
    "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d %b %y",
)
_CASTERS = {
    "full_name": str, "whatsapp_number": str, "nationality": str,
    "residence_country": str, "children_ages": str,
    "gcc_residence_card": lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1"),
    "travel_intent": lambda v: v if isinstance(v, bool) else str(v).strip().lower() in ("true", "yes", "1"),
    "trip_days": int, "adults": int,
}


def _parse_date(raw):
    if isinstance(raw, dt.datetime):
        return raw.date()
    if isinstance(raw, dt.date):
        return raw
    text = str(raw).strip()
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _coerce(fields: dict) -> dict:
    """Keep only valid, truthy values with the right python types."""
    clean = {}
    for key, caster in _CASTERS.items():
        value = fields.get(key)
        if value in (None, ""):
            continue
        try:
            value = caster(value)
        except (TypeError, ValueError):
            continue
        if value in (None, ""):
            continue
        clean[key] = value
    for key in ("residence_card_expiry", "travel_date"):
        parsed = _parse_date(fields[key]) if fields.get(key) else None
        if parsed:
            clean[key] = parsed
    return clean


def parse_extraction_json(raw: str) -> dict:
    """Tolerant JSON-object parse (handles code fences and stray prose)."""
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        data = json.loads(text[start:end + 1])
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_fields(text: str, send_fn=None) -> dict:
    """Return cleaned profile fields found in `text` ({} when none).

    `send_fn(messages, config)` defaults to the active LLMConfig's adapter;
    tests inject a stub instead of calling a real LLM.
    """
    text = (text or "").strip()
    if not text:
        return {}
    config = None
    if send_fn is None:
        config = LLMConfig.objects.filter(is_active=True).first()
        if config is None:
            return {}
        send_fn = get_adapter(config).send
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Today is {timezone.now():%Y-%m-%d}. Customer message(s):\n"
            f"\"\"\"\n{text}\n\"\"\"\nJSON:"
        )},
    ]
    try:
        raw = send_fn(messages, config)
    except Exception:
        logger.warning("Travel-profile extraction LLM call failed", exc_info=True)
        return {}
    return _coerce(parse_extraction_json(raw))


def apply_fields(customer, fields: dict) -> "TravelProfile":
    """Merge extracted fields into the customer's profile, creating it (and its
    BP number) on first detail. Also backfills Customer.display_name."""
    from profiles.models import ProfileNumberCounter, TravelProfile

    profile = getattr(customer, "travel_profile", None)
    if profile is None:
        profile = TravelProfile(customer=customer)
    # travel_intent is a state flag, not a detail: once True it never flips back
    # (a later "not travelling after all" must not un-detect earlier interest).
    if fields.pop("travel_intent", False):
        profile.travel_intent_detected = True
    for key, value in fields.items():
        setattr(profile, key, value)
    if not profile.profile_number:
        profile.profile_number = ProfileNumberCounter.next_profile_number()
    if fields.get("full_name") and customer.display_name in ("", "Website visitor"):
        customer.display_name = fields["full_name"]
        customer.save(update_fields=["display_name", "updated_at"])
    profile.refresh_completion()
    profile.save()
    return profile