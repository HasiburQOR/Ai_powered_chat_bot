import hashlib
import hmac
import json
import logging

from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.tasks import process_inbound_message
from platforms.models import Channel

logger = logging.getLogger(__name__)


@require_GET
def meta_verify(request):
    """Meta webhook verification handshake (GET /webhooks/meta/)."""
    mode = request.GET.get("hub.mode")
    token = request.GET.get("hub.verify_token")
    challenge = request.GET.get("hub.challenge", "")

    if mode != "subscribe" or not token:
        return HttpResponseForbidden("Bad verification request")

    # Check every active Meta channel's verify_token (shared Meta app is common).
    for channel in Channel.objects.filter(is_active=True, channel_type__in=["instagram", "messenger"]):
        if (channel.credentials or {}).get("verify_token") == token:
            return HttpResponse(challenge, content_type="text/plain")
    return HttpResponseForbidden("Verify token mismatch")


def _find_channel_by_signature(payload: bytes, signature: str):
    """Return the Meta channel whose app_secret validates X-Hub-Signature-256
    (or legacy X-Hub-Signature sha1), or None. Signature verification is
    mandatory in production — never trust unsigned payloads."""
    if not signature:
        return None

    channels = list(Channel.objects.filter(is_active=True, channel_type__in=["instagram", "messenger"]))

    if signature.startswith("sha256="):
        digest = signature.removeprefix("sha256=")
        hasher, algo_len = hashlib.sha256, 64
    elif signature.startswith("sha1="):
        digest = signature.removeprefix("sha1=")
        hasher, algo_len = hashlib.sha1, 40
    else:
        return None

    if len(digest) != algo_len:
        return None

    for channel in channels:
        secret = (channel.credentials or {}).get("app_secret")
        if not secret:
            continue
        expected = hmac.new(secret.encode(), payload, hasher).hexdigest()
        if hmac.compare_digest(expected, digest):
            return channel

    return None


@csrf_exempt
def meta_endpoint(request):
    """Single shared endpoint: GET = verification handshake, POST = event delivery."""
    if request.method == "GET":
        return meta_verify(request)
    return meta_webhook(request)


@csrf_exempt
@require_POST
def meta_webhook(request):
    """Event delivery (POST /webhooks/meta/). Verify signature, enqueue tasks,
    return 200 immediately — Meta retries aggressively on slow/non-200."""
    payload = request.body
    sig = request.headers.get("X-Hub-Signature-256") or request.headers.get("X-Hub-Signature", "")
    channel = _find_channel_by_signature(payload, sig)
    if channel is None:
        logger.warning("Rejected Meta webhook with bad/missing signature or unmatched channel")
        return HttpResponseForbidden("Invalid signature or channel")

    try:
        data = json.loads(payload)
    except ValueError:
        return HttpResponseForbidden("Invalid JSON")

    # Top-level object tells us which surface: "page" (Messenger) vs "instagram".
    obj = data.get("object")
    if obj == "page":
        channel_type = "messenger"
    elif obj == "instagram":
        channel_type = "instagram"
    else:
        return HttpResponse(status=200)  # Unknown object — ack and ignore.

    page_id = (channel.credentials or {}).get("page_id")
    for entry in data.get("entry", []):
        # Prefer the page_id actually present in the payload.
        entry_page_id = str(entry.get("id") or page_id or "")
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            text = message.get("text")
            sender_id = (event.get("sender") or {}).get("id")
            if not text or not sender_id:
                continue  # Non-text events (reactions, read receipts, etc.)
            process_inbound_message.delay(channel_type, entry_page_id, sender_id, text, raw_payload=event)

    return HttpResponse(status=200)
