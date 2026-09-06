"""Bot engine: the orchestration "brain".

Given a Conversation and new inbound text:
  1. Save inbound Message.
  2. Check rules; if a short-circuiting rule matches, save + return its response_text.
  3. Otherwise: pull short-term history, customer memory summary, retrieve
     relevant knowledge chunks, assemble the prompt, call the active LLMConfig.
  4. Save outbound Message, update Conversation.last_message_at.
  5. Enqueue memory summarization when the message-count threshold is crossed.
"""
from datetime import datetime  # noqa: F401
import logging

from django.utils import timezone

from conversations.models import Conversation, Message
from knowledge.models import BotSettings
from knowledge.retrieval import match_rule, retrieve_relevant_chunks
from llm.adapters import get_adapter
from llm.models import LLMConfig

logger = logging.getLogger(__name__)

FALLBACK_DEFAULT = "I'm not sure about that — let me get a team member to help."


def _settings() -> BotSettings:
    return BotSettings.load()


def _build_context_sections(customer, chunks) -> str:
    sections = []
    if customer and customer.memory_summary:
        sections.append(f"Known facts about this customer (long-term memory):\n{customer.memory_summary}")
    if chunks:
        knowledge = "\n\n".join(f"### {c.title}\n{c.content}" for c in chunks)
        sections.append(
            "Relevant knowledge base excerpts (use these to answer; "
            "if they don't cover the question, say so honestly):\n" + knowledge
        )
    return "\n\n".join(sections)


def handle_inbound_message(conversation: Conversation, text: str, raw_payload=None) -> Message:
    """Process one inbound customer message and return the bot's outbound Message."""
    settings = _settings()
    inbound = Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.CUSTOMER,
        content=text,
        raw_payload=raw_payload,
    )
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=["last_message_at"])

    # 2. Deterministic rules first.
    rule = match_rule(text)
    if rule and rule.short_circuits_llm:
        return Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content=rule.response_text,
        )

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
    messages = [{"role": "system", "content": config.system_prompt if config else "You are a helpful support assistant."}]
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

    outbound = Message.objects.create(
        conversation=conversation,
        sender_type=Message.SenderType.BOT,
        content=reply_text,
    )

    # 5. Memory summarization trigger.
    message_count = Message.objects.filter(conversation=conversation).count()
    trigger = settings.memory_summary_trigger_count or 20
    if message_count % trigger == 0:
        from bot.tasks import summarize_customer_memory

        summarize_customer_memory.delay(str(conversation.customer_id))

    return outbound
