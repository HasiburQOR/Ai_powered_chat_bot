"""LLM-driven extraction of travel-profile details from free visitor text."""
import datetime as dt
import json
import logging

from django.utils import timezone

from llm.adapters import LLMProviderError, get_adapter
from llm.models import LLMConfig

logger = logging.getLogger(__name__)

EXTRACTION_SYSTEM_PROMPT = """You read a chat window from a travel-agency conversation and extract trip-lead details the CUSTOMER stated.
Return a single JSON object. Include ONLY keys you actually found in the text:
  full_name (string), whatsapp_number (string), nationality (string),
  residence_country (string), gcc_residence_card (true/false),
  residence_card_expiry (YYYY-MM-DD), travel_date (YYYY-MM-DD),
  trip_days (integer), adults (integer), children_ages (string like "5, 8"),
  travel_intent (true/false: does the message express interest in a trip).
Rules:
- Fuzzy dates ("next month", "12 Oct", "in 3 weeks") become a best-effort YYYY-MM-DD using today's date; if impossible, omit the key.
- When the customer states an exact date ("december 16"), use that exact
  date — never shift it by a day or two.
- A year alone ("residence card expiry 2030") is not a full date: omit the
  key rather than inventing a month and day.
- travel_intent is true whenever the visitor asks about destinations, packages,
  prices, visas, flights, hotels or any trip planning — in ANY language.
- If a detail appears more than once with different values, use the most
  recent one (visitors correct themselves mid-conversation).
- Copy names and phone/WhatsApp numbers EXACTLY as the customer typed them —
  keep the leading "+" and country code; never rearrange, shorten or guess
  digits.
- A bare country code alone ("+880") is NOT a WhatsApp number — record
  whatsapp_number only when the customer also gives subscriber digits.
- When the customer gives a total party size plus children ("12 people, 2
  children aged 5 and 8"), adults = total minus children (→ adults: 10) and
  list every child's age in children_ages.
- Chat-window lines are labelled [Bot] or [Customer]. Extract details ONLY
  from [Customer] lines; use [Bot] lines only to understand what question an
  answer belongs to ("01712345678" right after the bot asks for WhatsApp IS
  the WhatsApp number). Unlabelled lines are customer lines.
- The customer may write in ANY language or script — extract the stated
  detail anyway; do not skip it because the language differs.
- Fields listed as "still missing" in the user message deserve extra
  attention — look hard for those, but still never invent them.
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


# Structured extraction needs a cold model: the chat temperature (often 0.7)
# made the extractor itself "creative" — visitors had to repeat details the
# bot then mis-parsed or dropped entirely.
EXTRACTION_TEMPERATURE = 0.1


def _call_extraction_llm(send_fn, messages, config):
    """JSON-mode call with a graceful fallback: not every OpenAI-compatible
    provider implements response_format, and one that rejects it answers
    HTTP 400 — retry the identical call without the option instead of
    dropping the whole extraction."""
    try:
        return send_fn(
            messages, config,
            temperature=EXTRACTION_TEMPERATURE,
            response_format={"type": "json_object"},
        )
    except LLMProviderError as exc:
        if "HTTP 400" in str(exc):
            return send_fn(messages, config, temperature=EXTRACTION_TEMPERATURE)
        raise


def extract_fields(text: str, send_fn=None, missing_fields=None) -> dict:
    """Return cleaned profile fields found in `text`.

    ``{}`` when the text cleanly contains nothing usable; ``None`` when the
    LLM call itself failed (timeout, or reasoning models answering HTTP 200
    with EMPTY content — seen live with glm-5.3-flash) so the caller can
    distinguish "nothing to save" from "try again". Tests inject `send_fn`
    instead of calling a real LLM. `missing_fields` (human labels) focuses
    the model on what the profile still lacks.
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
    focus = ""
    if missing_fields:
        focus = (
            "Still missing from the customer's profile (look extra hard for "
            f"these): {', '.join(missing_fields)}.\n"
        )
    messages = [
        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"Today is {timezone.now():%Y-%m-%d}. {focus}Chat window:\n"
            f"\"\"\"\n{text}\n\"\"\"\nJSON:"
        )},
    ]
    try:
        raw = _call_extraction_llm(send_fn, messages, config)
    except Exception:
        logger.warning("Travel-profile extraction LLM call failed", exc_info=True)
        return None  # transient — the task retries these
    fields = _coerce(parse_extraction_json(raw))
    if not fields:
        # A clean "nothing found". Logged so half-empty profiles are
        # diagnosable from the worker log alone (the live glm-5.3-flash
        # empty-content bug was invisible: no success line, no error).
        logger.info(
            "Travel-profile extraction found no usable details in: %.200s", text)
    return fields


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