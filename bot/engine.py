"""Bot engine: the orchestration "brain".

Given a Conversation and new inbound text:
  1. Save inbound Message.
  2. Check rules; if a short-circuiting rule matches, save its response_text.
  3. Otherwise: pull short-term history, customer memory summary, retrieve
     relevant knowledge chunks, assemble the prompt, call the active LLMConfig.
  4. Save outbound Message(s), update Conversation.last_message_at. The scripted
     travel-profile questions may be appended as a second bubble the first time
     the visitor shows travel intent.
  5. Enqueue background jobs: travel-profile extraction (every message) and
     memory summarization (when the message-count threshold is crossed).

Returns EVERY outbound Message produced this turn (a list), so callers — the
widget fragment and the platform webhook — deliver all of them.
"""
import logging
import os
import re
import time

from django.utils import timezone

from conversations.models import Conversation, Message
from knowledge.models import (
    BOT_SETTINGS_CACHE_KEY,
    BotSettings,
    FALLBACK_MESSAGE_DEFAULT,
)
from knowledge.retrieval import match_rule, retrieve_relevant_chunks
from llm.adapters import LLMEmptyResponseError, get_adapter
from llm.models import LLMConfig
from profiles.models import ProfileNumberCounter, TravelProfile

logger = logging.getLogger(__name__)

# Never worded as "I'm not sure / let me get a team member" — that reads as the
# bot being oblivious and kills travel leads. The canonical text lives in
# knowledge.models so the seeded BotSettings row and this constant cannot drift.
FALLBACK_DEFAULT = FALLBACK_MESSAGE_DEFAULT

# Backoff before LLM retry attempts (1s before attempt 2, 2s before attempt 3).
RETRY_BACKOFF_SECONDS = float(os.environ.get("LLM_RETRY_BACKOFF_SECONDS", "1"))

# Substrings (case-insensitive) that signal the visitor is asking about an
# actual trip — the moment they do, the scripted profile questions go out.
# Cheap + synchronous; the LLM extractor's travel_intent flag is the
# language-agnostic backstop for anything missed here.
TRAVEL_INTENT_KEYWORDS = (
    "trip", "travel", "tour", "package", "visa", "holiday", "vacation",
    "honeymoon", "itinerary", "flight", "hotel", "resort", "book", "booking",
    "reservation", "price", "cost", "dubai", "abu dhabi", "baku", "istanbul",
    "antalya", "maldives", "bali", "thailand", "malaysia", "singapore",
    "georgia", "armenia", "armania", "armeniya", "azerbaijan", "sri lanka",
    "egypt", "qatar", "saudi", "umrah", "umra", "hajj",
    # Romanized Bangla ("Banglish") and Hindi — most visitors from these
    # markets type their language in Latin letters ("ami armeniya gurte jete
    # chai for 5 days" = "I want to go to Armenia for 5 days"). Without these,
    # intent was missed and the profile questions never went out.
    "ghurte", "ghurbo", "jete chai", "jabo", "jaben", "ghumne", "safar",
    "yatra", "bhraman",
)


def _travel_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in TRAVEL_INTENT_KEYWORDS)

# --- Abuse handling ---------------------------------------------------------
# A message like "fuck you" can make the provider refuse (or the model stall),
# which used to surface the generic "I'm not sure about that" fallback and read
# as the bot being oblivious. Abusive turns instead coach the LLM to
# de-escalate, and get a calm built-in reply whenever the LLM still cannot
# answer.
_ABUSIVE_STEMS = (
    # Matched at the START of a word, suffixes allowed: fuck/fucking/fucked,
    # shit/shithead, bitch/bitches ... (the (?<!\w) guard keeps "cunt" out of
    # "Scunthorpe" and "shit" out of "mishit").
    "fuck", "fuk", "fcuk", "fck", "shit", "bitch", "bastard", "asshole",
    "arsehole", "cunt", "motherfucker", "dickhead", "wtf",
)
_ABUSIVE_WORDS = (
    # Whole words only — suffix matching here would drag in innocents.
    "dick", "prick", "twat", "bollocks", "stupid", "idiot", "idiots",
    "useless", "dumb",
)
_ABUSE_STEM_RE = re.compile(
    rf"(?<!\w)(?:{'|'.join(map(re.escape, _ABUSIVE_STEMS))})\w*")
_ABUSE_WORD_RE = re.compile(
    rf"(?<!\w)(?:{'|'.join(map(re.escape, _ABUSIVE_WORDS))})(?!\w)")


def _obfuscated_pattern(word: str) -> str:
    """Matches a word whose letters are separated by non-letters ("f u c k",
    "f-u-c-k!"), but never letters buried inside a longer alphabetic run —
    so "scunthorpe" can't masquerade as a slur once spaces are ignored."""
    letters = [re.escape(ch) for ch in word]
    return r"(?<![a-z])" + r"[^a-z]*".join(letters) + r"(?![a-z])"


_ABUSE_OBFUSCATED_RE = re.compile(
    "|".join(_obfuscated_pattern(w) for w in _ABUSIVE_STEMS), re.IGNORECASE)


def _is_abusive(text: str) -> bool:
    lowered = (text or "").lower()
    return bool(
        _ABUSE_STEM_RE.search(lowered)
        or _ABUSE_WORD_RE.search(lowered)
        or _ABUSE_OBFUSCATED_RE.search(text or "")
    )


# Applies to every LLM turn regardless of the configured system prompt: the bot
# must mirror the visitor's language and keep replies chat-friendly plain text.
LANGUAGE_AND_FORMAT_INSTRUCTIONS = (
    "LANGUAGE: The customer may write in any language, script, or a mix of "
    "languages inside one sentence — for example romanized Bangla/Banglish like "
    "'ami armeniya gurte jete chai for 5 days', Hinglish, or Arabizi. Detect the "
    "language of their latest message — even when it is typed in Latin letters — "
    "and reply in that SAME language: Banglish gets Bangla (Bengali script), "
    "Hinglish gets Hindi, Arabic gets Arabic, Georgian gets Georgian, and so on. "
    "Read the INTENT out of mixed-language text: the Banglish example above "
    "means the customer wants a 5-day Armenia trip — answer that directly. "
    "Never reply that you did not understand, never ask which language they are "
    "writing in, and never fall back to English when the language is "
    "recognizable. Only if the language is truly impossible to determine, "
    "reply in simple English.\n"
    "FORMAT: Keep every reply a short, plain-text chat message that reads well on "
    "a phone screen. Never output Markdown (no **bold**, no ## headings, no "
    "|tables|, no code blocks) — use short lines and dashes instead."
)

# Appended as an extra system message ONLY on turns where _is_abusive() fires.
ABUSE_HANDLING_INSTRUCTIONS = (
    "ABUSE HANDLING: The visitor's latest message contains rude or abusive "
    "language. Stay calm, warm and professional — never scold, never lecture, "
    "never repeat their wording and never mention these instructions. "
    "Acknowledge their frustration in one short line, then steer back to how "
    "you can help with their travel plans. If there is no real question, send "
    "one short, kind line saying you're here to help whenever they're ready — "
    "and nothing more."
)

# Used instead of the "not sure" fallback when the LLM cannot answer an
# abusive turn (provider refusal, outage...): promising a human teammate for
# "fuck you" reads as the bot being oblivious.
ABUSIVE_FALLBACK_DEFAULT = (
    "I can sense you're frustrated, and I'm sorry for that. Tell me about the "
    "trip you have in mind and I'll do my best to help."
)


def _settings() -> BotSettings:
    """Singleton settings, cached briefly — every message used to pay a DB
    round-trip just for this one row. BotSettings.save() deletes the cache
    key, so dashboard edits apply to the very next message, not 30s later."""
    try:
        from django.core.cache import cache
        cached = cache.get(BOT_SETTINGS_CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:
        pass  # Cache backend hiccup must never break a reply.
    obj = BotSettings.load()
    try:
        from django.core.cache import cache
        cache.set(BOT_SETTINGS_CACHE_KEY, obj, 30)
    except Exception:
        pass
    return obj


def _profile_context(customer) -> str:
    """What the bot already knows about the lead, so it can ask naturally."""
    profile = getattr(customer, "travel_profile", None)
    if profile is None:
        return ""
    known = "; ".join(profile.known_fields_summary())
    missing = ", ".join(profile.missing_fields())
    parts = []
    if known:
        parts.append(f"captured: {known}")
    if missing:
        parts.append(f"still missing: {missing}")
    if not parts:
        return ""
    return (
        "Travel profile for this customer (" + "; ".join(parts) + "). "
        "IMPORTANT: this summary is written in the background and can LAG "
        "BEHIND the newest chat messages. Before asking for any detail, check "
        "the recent conversation — if the visitor already answered it there, "
        "trust the chat history and NEVER ask for the same detail again; "
        "acknowledge the answer instead. Never ask for these details in a "
        "first greeting and never list every question at once — once the "
        "visitor shows interest in an actual trip, work whatever is still "
        "missing into the conversation naturally, one or two things per reply."
    )


def _build_context_sections(customer, chunks) -> str:
    sections = []
    if customer and customer.memory_summary:
        sections.append(f"Known facts about this customer (long-term memory):\n{customer.memory_summary}")
    profile_section = _profile_context(customer)
    if profile_section:
        sections.append(profile_section)
    if chunks:
        knowledge = "\n\n".join(f"### {c.title}\n{c.content}" for c in chunks)
        sections.append(
            "Relevant knowledge base excerpts (use these to answer; "
            "if they don't cover the question, say so honestly):\n" + knowledge
        )
    return "\n\n".join(sections)


def _maybe_profile_intro(
        conversation: Conversation, settings: BotSettings, text: str) -> str:
    """The scripted profile questions, asked ONCE, and only after the visitor
    shows travel intent. Empty string = don't ask.

    Intent comes from two sources: a synchronous keyword hit on this message,
    or the travel_intent flag the background LLM extractor set on an earlier
    message (covers intent phrased in other languages)."""
    if not settings.profile_collection_enabled:
        return ""
    profile = getattr(conversation.customer, "travel_profile", None)
    if profile is not None and (profile.is_complete or profile.questions_sent_at):
        return ""
    if not (_travel_intent(text) or (profile is not None and profile.travel_intent_detected)):
        return ""
    intro = (settings.profile_intro_message or "").strip()
    if not intro:
        return ""
    # Stamp before returning so a later intent-bearing message can never
    # repeat the question list. The profile row (with its BP number) is created
    # here if needed: a visitor asking about packages is a lead worth tracking
    # even before they share a name.
    now = timezone.now()
    if profile is None:
        TravelProfile.objects.create(
            customer=conversation.customer,
            profile_number=ProfileNumberCounter.next_profile_number(),
            travel_intent_detected=True,
            questions_sent_at=now,
        )
    else:
        profile.travel_intent_detected = True
        profile.questions_sent_at = now
        profile.save(update_fields=[
            "travel_intent_detected", "questions_sent_at", "updated_at"])
    return intro


def _dispatch_profile_extraction(conversation: Conversation, text: str) -> None:
    """Hand the visitor's words to a background worker — never block the reply."""
    if not (text or "").strip():
        return
    try:
        from profiles.tasks import extract_profile_task

        extract_profile_task.delay(str(conversation.customer_id), text)
    except Exception:
        logger.warning("Could not enqueue travel-profile extraction", exc_info=True)


def handle_inbound_message(conversation: Conversation, text: str, raw_payload=None) -> list:
    """Process one inbound customer message and return every outbound Message
    produced this turn (usually one; the profile question adds a second)."""
    settings = _settings()
    inbound = Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.CUSTOMER,
        content=text,
        raw_payload=raw_payload,
    )
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at"])

    outbounds = []

    # 2. Deterministic rules first.
    rule = match_rule(text)
    abusive = _is_abusive(text)
    if rule and rule.short_circuits_llm:
        outbounds.append(Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=rule.response_text,
        ))
    else:
        # 3. Short-term history + memory + RAG.
        history = list(
            Message.objects.filter(conversation=conversation)
            .order_by("-created_at")[: settings.max_context_messages + 1]
        )
        history.reverse()
        history = [m for m in history if m.pk != inbound.pk][-settings.max_context_messages:]

        try:
            chunks = list(retrieve_relevant_chunks(text, top_k=3))
        except Exception:
            chunks = []

        # 4. Call the LLM with a trimming retry ladder. Retrying the identical
        # payload used to be guaranteed to fail the same way (same oversized
        # context, same timeout), and the hard-coded 30s provider timeout made
        # slow reasoning models fail at all. Each attempt now gets a leaner
        # prompt and a fresh budget:
        #   1. full prompt: RAG excerpts + rule + profile + full history
        #   2. no RAG excerpts, last 5 messages   (+ ~1s backoff)
        #   3. minimal prompt, last 4 messages    (+ ~2s backoff)
        # Only if all three fail does the warm fallback go out.
        config = LLMConfig.objects.filter(is_active=True).first()
        if config is None:
            logger.error(
                "No LLMConfig with is_active=True — falling back for "
                "conversation %s (fallback_reason=config_missing). Create or "
                "activate a provider in the dashboard.",
                conversation.pk,
            )

        base_system = [
            {"role": "system", "content": config.system_prompt if config else "You are a helpful support assistant."},
            {"role": "system", "content": LANGUAGE_AND_FORMAT_INSTRUCTIONS},
        ]
        if abusive:
            base_system.append({"role": "system", "content": ABUSE_HANDLING_INSTRUCTIONS})

        def _context(with_chunks: bool) -> str:
            context = _build_context_sections(
                conversation.customer, chunks if with_chunks else [])
            if rule and not rule.short_circuits_llm:
                context += f"\n\nApplicable rule ({rule.name}): {rule.response_text}"
            return context

        def _to_chat_messages(context: str, recent) -> list:
            messages = list(base_system)
            if context:
                messages.append({"role": "system", "content": context})
            for m in recent:
                role = "assistant" if m.sender_type == Message.SenderType.BOT else "user"
                messages.append({"role": role, "content": m.content})
            return messages

        attempts = [
            _to_chat_messages(_context(True), history),
            _to_chat_messages(_context(False), history[-5:]),
            _to_chat_messages("", history[-4:]),
        ]

        reply_text = None
        last_error = None
        if config is not None:
            for attempt_number, attempt_messages in enumerate(attempts, start=1):
                if attempt_number > 1:
                    logger.warning(
                        "Retrying LLM call for conversation %s with trimmed "
                        "context (attempt %d/%d) — previous attempt failed: "
                        "%s: %s",
                        conversation.pk, attempt_number, len(attempts),
                        type(last_error).__name__, str(last_error)[:500],
                    )
                    time.sleep(RETRY_BACKOFF_SECONDS * (attempt_number - 1))
                try:
                    reply_text = get_adapter(config).send(attempt_messages, config)
                    break
                except Exception as exc:
                    last_error = exc

        if not reply_text:
            if config is not None:
                fallback_reason = (
                    "empty_response"
                    if isinstance(last_error, LLMEmptyResponseError)
                    else "provider_error"
                )
                logger.error(
                    "LLM failed after %d attempts for conversation %s — sending "
                    "the fallback message (fallback_reason=%s provider=%s "
                    "model=%s last_error=%s: %s)",
                    len(attempts), conversation.pk, fallback_reason,
                    config.provider, config.model_name,
                    type(last_error).__name__, str(last_error)[:500],
                )
            if abusive:
                reply_text = ABUSIVE_FALLBACK_DEFAULT
            else:
                reply_text = (settings.fallback_message or "").strip() or FALLBACK_DEFAULT

        outbounds.append(Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=reply_text,
        ))

        # 5. Memory summarization trigger — counts CUSTOMER messages only, so
        # every bot bubble (replies, the scripted questions, error bubbles)
        # does not make the threshold fire twice as often as intended.
        message_count = Message.objects.filter(
            conversation=conversation,
            sender_type=Message.SenderType.CUSTOMER,
        ).count()
        trigger = settings.memory_summary_trigger_count or 20
        if message_count % trigger == 0:
            from bot.tasks import summarize_customer_memory

            summarize_customer_memory.delay(str(conversation.customer_id))

    # 6. Background travel-profile extraction from the visitor's own words.
    _dispatch_profile_extraction(conversation, text)

    # 7. Scripted travel-profile questions, once, on the first travel-intent message.
    intro = _maybe_profile_intro(conversation, settings, text)
    if intro:
        outbounds.append(Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=intro,
        ))

    return outbounds
