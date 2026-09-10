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

from django.utils import timezone

from conversations.models import Conversation, Message
from knowledge.models import BotSettings
from knowledge.retrieval import match_rule, retrieve_relevant_chunks
from llm.adapters import get_adapter
from llm.models import LLMConfig
from profiles.models import ProfileNumberCounter, TravelProfile

logger = logging.getLogger(__name__)

FALLBACK_DEFAULT = "I'm not sure about that — let me get a team member to help."

# Substrings (case-insensitive) that signal the visitor is asking about an
# actual trip — the moment they do, the scripted profile questions go out.
# English-only by design (cheap + synchronous); the LLM extractor's
# travel_intent flag is the language-agnostic backstop for anything missed.
TRAVEL_INTENT_KEYWORDS = (
    "trip", "travel", "tour", "package", "visa", "holiday", "vacation",
    "honeymoon", "itinerary", "flight", "hotel", "resort", "book", "booking",
    "reservation", "price", "cost", "dubai", "abu dhabi", "baku", "istanbul",
    "antalya", "maldives", "bali", "thailand", "malaysia", "singapore",
    "georgia", "armenia", "azerbaijan", "sri lanka", "egypt", "qatar",
    "saudi", "umrah", "umra", "hajj",
)


def _travel_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in TRAVEL_INTENT_KEYWORDS)

# Applies to every LLM turn regardless of the configured system prompt: the bot
# must mirror the visitor's language and keep replies chat-friendly plain text.
LANGUAGE_AND_FORMAT_INSTRUCTIONS = (
    "LANGUAGE: The customer may write in any language. Always reply in the same "
    "language and script the customer used in their latest message — Hindi (in "
    "Latin or Devanagari script) gets a Hindi reply, Arabic gets Arabic, Georgian "
    "gets Georgian, and so on. Only if you truly cannot determine the language, "
    "reply in simple English.\n"
    "FORMAT: Keep every reply a short, plain-text chat message that reads well on "
    "a phone screen. Never output Markdown (no **bold**, no ## headings, no "
    "|tables|, no code blocks) — use short lines and dashes instead."
)


def _settings() -> BotSettings:
    return BotSettings.load()


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
        "Never ask for these details in a first greeting and never list every "
        "question at once — once the visitor shows interest in an actual trip, "
        "work whatever is still missing into the conversation naturally, one or "
        "two things per reply."
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

        # Assemble prompt.
        config = LLMConfig.objects.filter(is_active=True).first()
        messages = [
            {"role": "system", "content": config.system_prompt if config else "You are a helpful support assistant."},
            {"role": "system", "content": LANGUAGE_AND_FORMAT_INSTRUCTIONS},
        ]
        context = _build_context_sections(conversation.customer, chunks)
        if rule and not rule.short_circuits_llm:
            context += f"\n\nApplicable rule ({rule.name}): {rule.response_text}"
        if context:
            messages.append({"role": "system", "content": context})
        for m in history:
            role = "assistant" if m.sender_type == Message.SenderType.BOT else "user"
            messages.append({"role": role, "content": m.content})

        # 4. Call the LLM.
        reply_text = None
        if config is not None:
            try:
                reply_text = get_adapter(config).send(messages, config)
            except Exception as exc:
                logger.error(
                    "LLM call failed for conversation %s (provider=%s model=%s): %s: %s",
                    conversation.pk, config.provider, config.model_name,
                    type(exc).__name__, str(exc)[:500],
                )
                reply_text = None
        if not reply_text:
            reply_text = settings.fallback_message or FALLBACK_DEFAULT

        outbounds.append(Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=reply_text,
        ))

        # 5. Memory summarization trigger.
        message_count = Message.objects.filter(conversation=conversation).count()
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
