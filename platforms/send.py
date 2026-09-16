"""Outbound senders for each platform.

Endpoints/permissions shift between Graph API versions — check the current
Meta for Developers docs when deploying. Messenger and Instagram use the
same /me/messages shape under the same Meta app. WhatsApp (Cloud API)
sends via /<phone_number_id>/messages with a Bearer token instead.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
MESSAGES_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"
WHATSAPP_MESSAGES_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/{{phone_number_id}}/messages"

# WhatsApp text bubbles are capped at 4096 characters; longer bot replies
# must be split across multiple bubbles.
WHATSAPP_TEXT_LIMIT = 4096

# The bot's prompt now asks for **bold** key facts (the chat widget renders
# them as real <strong>). Meta channels render plain text only, so the
# markers would reach Messenger/Instagram visitors as literal asterisks.
_BOLD_PAIR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _plain_text(text: str) -> str:
    """Strip **bold** markers (keeping the wrapped words), then drop any
    stray unpaired '**' left over."""
    return _BOLD_PAIR_RE.sub(r"\1", str(text or "")).replace("**", "")


def _wa_text(text: str) -> str:
    """Rewrite the bot's **bold** markers into WhatsApp's native *bold*
    syntax (WhatsApp renders single asterisks, not double), then drop any
    stray unpaired '**' left over."""
    return _BOLD_PAIR_RE.sub(r"*\1*", str(text or "")).replace("**", "")


def _chunk_text(text: str, limit: int = WHATSAPP_TEXT_LIMIT):
    """Yield pieces of at most `limit` characters, preferring newline
    boundaries so split bubbles still read naturally."""
    text = text or ""
    if len(text) <= limit:
        yield text
        return
    buf = ""
    for line in text.splitlines(keepends=True):
        while len(line) > limit:  # A single line longer than a whole bubble.
            if buf:
                yield buf
                buf = ""
            yield line[:limit]
            line = line[limit:]
        if buf and len(buf) + len(line) > limit:
            yield buf
            buf = ""
        buf += line
    if buf:
        yield buf


def _send_graph_message(access_token, recipient_id, text):
    resp = requests.post(
        MESSAGES_URL,
        params={"access_token": access_token},
        json={"recipient": {"id": recipient_id}, "message": {"text": text}},
        timeout=30,
    )
    if not resp.ok:
        logger.error("Graph send failed [%s]: %s", resp.status_code, resp.text)
    return resp.ok


def _send_whatsapp_message(access_token, phone_number_id, to, text):
    """Cloud API text send. Free-form text is only allowed inside the 24-hour
    customer-service window after the user's last inbound message — the bot
    always replies right after an inbound, so that is the normal case; outside
    the window Meta rejects with 4xx, which we log and move on (pre-approved
    template messages would be the fix, if ever needed)."""
    resp = requests.post(
        WHATSAPP_MESSAGES_URL.format(phone_number_id=phone_number_id),
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        },
        timeout=30,
    )
    if not resp.ok:
        logger.error("WhatsApp send failed [%s]: %s", resp.status_code, resp.text)
    return resp.ok


def send_platform_reply(channel, recipient_id, text):
    """Dispatch an outbound reply via the channel's stored credentials."""
    creds = channel.credentials or {}

    if channel.channel_type in ("instagram", "messenger"):
        token = creds.get("page_access_token")
        if not token:
            logger.error("Channel %s has no page_access_token", channel.pk)
            return False
        return _send_graph_message(token, recipient_id, _plain_text(text))

    if channel.channel_type == "whatsapp":
        token = creds.get("access_token")
        phone_number_id = creds.get("phone_number_id")
        if not token or not phone_number_id:
            logger.error("Channel %s has no access_token/phone_number_id", channel.pk)
            return False
        # WhatsApp bold is single asterisks and bubbles cap at 4096 chars.
        ok = True
        for part in _chunk_text(_wa_text(text)):
            ok = _send_whatsapp_message(token, phone_number_id, recipient_id, part) and ok
        return ok

    logger.warning("send_platform_reply called for unsupported channel %s", channel.pk)
    return False
