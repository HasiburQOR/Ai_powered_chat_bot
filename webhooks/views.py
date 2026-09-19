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
        # Not Meta (Meta always sends hub.mode + hub.verify_token) — almost
        # always a bot/scanner probe. Log the UA so stray 403s are attributable.
        logger.warning(
            "Meta webhook rejected: bad verification request "
            "(method=%s mode=%r token_present=%s ua=%r)",
            request.method, mode, bool(token), request.headers.get("User-Agent", ""),
        )
        return HttpResponseForbidden("Bad verification request")

    # Strip stray whitespace on both sides — a verify token pasted into the
    # credentials JSON with a trailing newline would otherwise never match.
    token = token.strip()

    # Check every active Meta channel's verify_token (shared Meta app is common;
    # the WhatsApp Cloud API uses the same hub.* handshake).
    for channel in Channel.objects.filter(is_active=True, channel_type__in=["instagram", "messenger", "whatsapp"]):
        stored = str((channel.credentials or {}).get("verify_token") or "").strip()
        if stored and hmac.compare_digest(stored, token):
            return HttpResponse(challenge, content_type="text/plain")
    logger.warning("Meta webhook verification failed: verify token mismatch "
                   "(method=%s; check verify_token in the dashboard channel credentials)",
                   request.method)
    return HttpResponseForbidden("Verify token mismatch")


def _find_channel_by_signature(payload: bytes, signature: str, method: str = ""):
    """Return the Meta channel whose app_secret validates X-Hub-Signature-256
    (or legacy X-Hub-Signature sha1), or None. Signature verification is
    mandatory in production — never trust unsigned payloads."""
    if not signature:
        logger.warning("Meta webhook rejected: X-Hub-Signature-256 header missing — "
                       "if Meta is definitely calling, the reverse proxy may be stripping it "
                       "(method=%s)", method)
        return None

    channels = list(Channel.objects.filter(is_active=True, channel_type__in=["instagram", "messenger", "whatsapp"]))

    if signature.startswith("sha256="):
        digest = signature.removeprefix("sha256=")
        hasher, algo_len = hashlib.sha256, 64
    elif signature.startswith("sha1="):
        digest = signature.removeprefix("sha1=")
        hasher, algo_len = hashlib.sha1, 40
    else:
        logger.warning("Meta webhook rejected: unrecognized signature format %r (method=%s)",
                       signature[:16], method)
        return None

    if len(digest) != algo_len:
        logger.warning("Meta webhook rejected: %s digest has wrong length %d (expected %d) (method=%s)",
                       hasher().name, len(digest), algo_len, method)
        return None

    with_secret = 0
    for channel in channels:
        # str()+strip(): an app_secret pasted into the credentials JSON with a
        # trailing newline/space signs differently and would never match.
        secret = str((channel.credentials or {}).get("app_secret") or "").strip()
        if not secret:
            continue
        with_secret += 1
        expected = hmac.new(secret.encode(), payload, hasher).hexdigest()
        if hmac.compare_digest(expected, digest):
            return channel

    # Diagnostics only — the secrets themselves are NEVER logged.
    logger.warning(
        "Meta webhook rejected: signature matched no active channel "
        "(method=%s). active_meta_channels=%d channels_with_app_secret_key=%d "
        "(causes: stored app_secret differs from the Meta app's App Secret, "
        "the 'app_secret' key is missing/misspelled in the credentials JSON, "
        "or the channel holding it is deactivated)",
        method, len(channels), with_secret,
    )
    return None


@csrf_exempt
def meta_endpoint(request):
    """Single shared endpoint: GET = verification handshake, POST = event delivery."""
    # Fires on EVERY request before any validation — proves traffic reaches the
    # view at all, regardless of outcome (200/403/405). This is the definitive
    # signal for "did a POST ever arrive?", separate from "was it rejected?".
    logger.info("Meta webhook request received: method=%s path=%s", request.method, request.path)
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
    channel = _find_channel_by_signature(payload, sig, method=request.method)
    if channel is None:
        logger.warning("Rejected Meta webhook with bad/missing signature or unmatched channel "
                       "(method=%s)", request.method)
        return HttpResponseForbidden("Invalid signature or channel")

    try:
        data = json.loads(payload)
    except ValueError:
        logger.warning("Meta webhook rejected: payload is not valid JSON after a "
                       "valid signature (method=%s channel=%s bytes=%d)",
                       request.method, channel.pk, len(payload))
        return HttpResponseForbidden("Invalid JSON")

    # Top-level object tells us which surface: "page" (Messenger),
    # "instagram", or "whatsapp_business_account" (WhatsApp Cloud API).
    obj = data.get("object")
    if obj == "page":
        _enqueue_messaging_events(data, "messenger", channel)
    elif obj == "instagram":
        _enqueue_messaging_events(data, "instagram", channel)
    elif obj == "whatsapp_business_account":
        _enqueue_whatsapp_events(data)
    # Unknown object — ack and ignore (Meta retries non-200 aggressively).

    # Visible acceptance: a silent 200 made it impossible to tell whether
    # Meta's traffic was reaching the app at all.
    logger.info("Meta webhook accepted: object=%s channel=%s bytes=%d (method=%s)",
                obj, channel.pk, len(payload), request.method)
    return HttpResponse(status=200)


def _enqueue_messaging_events(data, channel_type, channel):
    """Messenger / Instagram shape: entry[].messaging[]."""
    page_id = (channel.credentials or {}).get("page_id")
    for entry in data.get("entry", []):
        # Prefer the page_id actually present in the payload.
        entry_page_id = str(entry.get("id") or page_id or "")
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            if message.get("is_echo"):
                # Our own outbound reply mirrored back (Instagram always sends
                # these; Messenger does when message_echoes is subscribed).
                # Processing it would make the bot answer itself.
                continue
            text = message.get("text")
            sender_id = (event.get("sender") or {}).get("id")
            if not text or not sender_id:
                continue  # Non-text events (reactions, read receipts, etc.)
            process_inbound_message.delay(channel_type, entry_page_id, sender_id, text, raw_payload=event)


def _enqueue_whatsapp_events(data):
    """WhatsApp Cloud API shape: entry[].changes[].value.messages[].

    The channel is identified by metadata.phone_number_id (the business
    number that received the message); the sender by message["from"] (the
    customer's wa_id / phone number). Only type == "text" messages are
    handled in v1 — images, audio, locations etc. are acked and ignored.
    contacts[0].profile.name carries the customer's WhatsApp profile name,
    passed along so the task can label the Customer record."""
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
            contacts = value.get("contacts") or [{}]
            profile_name = (contacts[0].get("profile") or {}).get("name") or ""
            for message in value.get("messages") or []:
                if message.get("type") != "text":
                    continue  # Non-text message (image, audio, location, ...)
                text = ((message.get("text") or {}).get("body") or "").strip()
                sender_id = message.get("from")
                if not text or not sender_id or not phone_number_id:
                    continue
                process_inbound_message.delay(
                    "whatsapp", str(phone_number_id), sender_id, text,
                    raw_payload=message, profile_name=profile_name,
                )
