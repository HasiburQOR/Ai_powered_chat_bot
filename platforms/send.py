"""Outbound senders for each platform.

Endpoints/permissions shift between Graph API versions — check the current
Meta for Developers docs when deploying. Messenger and Instagram use the
same /me/messages shape under the same Meta app.
"""
import logging
import re

import requests

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v19.0"
MESSAGES_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}/me/messages"

# The bot's prompt now asks for **bold** key facts (the chat widget renders
# them as real <strong>). Meta channels render plain text only, so the
# markers would reach Messenger/Instagram visitors as literal asterisks.
_BOLD_PAIR_RE = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)


def _plain_text(text: str) -> str:
    """Strip **bold** markers (keeping the wrapped words), then drop any
    stray unpaired '**' left over."""
    return _BOLD_PAIR_RE.sub(r"\1", str(text or "")).replace("**", "")


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


def send_platform_reply(channel, recipient_id, text):
    """Dispatch an outbound reply via the channel's stored credentials."""
    if channel.channel_type not in ("instagram", "messenger"):
        logger.warning("send_platform_reply called for non-Meta channel %s", channel.pk)
        return False
    token = (channel.credentials or {}).get("page_access_token")
    if not token:
        logger.error("Channel %s has no page_access_token", channel.pk)
        return False
    return _send_graph_message(token, recipient_id, _plain_text(text))
