import uuid

from django import forms
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt

from bot.engine import handle_inbound_message
from conversations.models import Conversation, Customer, Message
from platforms.models import Channel

# Phase 10 hardening: basic per-session throttle on the send endpoint.
RATE_LIMIT_MESSAGES = 20
RATE_LIMIT_SECONDS = 60

_INPUT_CLASS = (
    "w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 "
    "placeholder:text-gray-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
)


class LeadDetailsForm(forms.Form):
    """Pre-chat capture — the visitor's contact details before the bot starts.

    Name and email are required ("the necessary details"); phone is optional.
    """

    name = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLASS, "placeholder": "Jane Doe", "autocomplete": "name",
        }),
    )
    email = forms.EmailField(
        widget=forms.EmailInput(attrs={
            "class": _INPUT_CLASS, "placeholder": "jane@example.com", "autocomplete": "email",
        }),
    )
    phone = forms.CharField(
        max_length=50,
        required=False,
        widget=forms.TextInput(attrs={
            "class": _INPUT_CLASS, "placeholder": "+1 555 123 4567 (optional)", "autocomplete": "tel",
        }),
    )


def _get_wordpress_channel(site_key):
    for channel in Channel.objects.filter(is_active=True, channel_type="wordpress"):
        if (channel.credentials or {}).get("site_key") == site_key:
            return channel
    return None


def embed_js(request):
    """Vanilla-JS snippet that runs on the host WordPress page. It reads
    data-site-key (required) plus optional data-position="left|right"
    (default right) and data-theme="#rrggbb" off its own <script> tag and
    injects a floating button plus a hidden iframe pointing back at our
    /widget/chat/ page."""
    origin = request.scheme + "://" + request.get_host()
    js = """
(function () {
  var script = document.currentScript || (function () {
    var s = document.getElementsByTagName('script');
    return s[s.length - 1];
  })();
  var siteKey = script.getAttribute('data-site-key');
  if (!siteKey) { console.error('chatbot embed.js: missing data-site-key'); return; }
  var origin = '%(origin)s';
  var position = (script.getAttribute('data-position') || 'right').toLowerCase() === 'left' ? 'left' : 'right';
  var side = position === 'left' ? 'left:20px;' : 'right:20px;';
  var theme = script.getAttribute('data-theme') || '#4f46e5';

  var iframe = document.createElement('iframe');
  iframe.src = origin + '/widget/chat/?site_key=' + encodeURIComponent(siteKey);
  iframe.style.cssText = 'display:none;position:fixed;bottom:90px;' + side + 'width:360px;height:520px;max-height:80vh;border:1px solid #d1d5db;border-radius:12px;box-shadow:0 8px 24px rgba(0,0,0,.18);z-index:2147483000;background:#fff;';

  var button = document.createElement('button');
  button.innerHTML = '&#128172;';
  button.style.cssText = 'position:fixed;bottom:20px;' + side + 'width:60px;height:60px;border-radius:9999px;border:none;background:' + theme + ';color:#fff;font-size:22px;cursor:pointer;z-index:2147483000;box-shadow:0 4px 12px rgba(0,0,0,.25);';
  button.setAttribute('aria-label', 'Open chat');

  button.addEventListener('click', function () {
    var open = iframe.style.display !== 'none';
    iframe.style.display = open ? 'none' : 'block';
  });

  document.body.appendChild(iframe);
  document.body.appendChild(button);
})();
""" % {"origin": origin}
    return HttpResponse(js, content_type="application/javascript")


@xframe_options_exempt
def chat(request):
    """Full chat UI rendered inside the iframe. Embedding is scoped via a
    dynamic Content-Security-Policy frame-ancestors header."""
    site_key = request.GET.get("site_key", "")
    channel = _get_wordpress_channel(site_key)
    if channel is None:
        return HttpResponseForbidden("Unknown or inactive site key.")

    allowed_domain = (channel.credentials or {}).get("allowed_domain", "")
    session_id = request.COOKIES.get(f"widget_session_{site_key}", "")
    if not session_id:
        session_id = uuid.uuid4().hex
    customer, _ = Customer.objects.get_or_create(
        channel=channel, external_id=session_id, defaults={"display_name": "Website visitor"}
    )
    conversation = customer.conversations.order_by("-last_message_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(customer=customer, last_message_at=timezone.now())

    collect_details = bool((channel.credentials or {}).get("collect_lead_details", True))
    context = _chat_panel_context(channel, conversation, session_id, site_key)
    # First-time visitors must provide contact details before the chat opens
    # (unless the channel opts out via collect_lead_details: false).
    context["needs_details"] = collect_details and not customer.email
    if context["needs_details"]:
        context["form"] = LeadDetailsForm()

    response = render(
        request,
        "widget/chat.html",
        context,
    )
    if allowed_domain:
        response["Content-Security-Policy"] = f"frame-ancestors {allowed_domain}"
    response.set_cookie(f"widget_session_{site_key}", session_id, max_age=60 * 60 * 24 * 365, samesite="Lax")
    return response


def _chat_panel_context(channel, conversation, session_id, site_key) -> dict:
    """Shared context for the chat page and the chat-panel partial."""
    return {
        "channel": channel,
        "conversation": conversation,
        "messages": conversation.messages.all(),
        "session_id": session_id,
        "site_key": site_key,
        "welcome_message": (channel.credentials or {}).get("welcome_message", "Hi! How can we help?"),
        "theme_color": (channel.credentials or {}).get("theme_color", "#4f46e5"),
    }


@csrf_exempt
@xframe_options_exempt
def submit_details(request, session_id):
    """HTMX endpoint for the pre-chat lead form. Saves the visitor's contact
    details on their Customer row and swaps in the full chat UI (hx-target
    '#widget-root'). Re-renders the form with errors on invalid input (422)."""
    if request.method != "POST":
        return HttpResponseForbidden("POST only")

    site_key = request.POST.get("site_key", "")
    channel = _get_wordpress_channel(site_key)
    if channel is None:
        return HttpResponseForbidden("Unknown or inactive site key.")

    customer, _ = Customer.objects.get_or_create(
        channel=channel, external_id=session_id, defaults={"display_name": "Website visitor"}
    )
    form = LeadDetailsForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "widget/partials/lead_form.html",
            {
                "form": form,
                "channel": channel,
                "session_id": session_id,
                "site_key": site_key,
                "theme_color": (channel.credentials or {}).get("theme_color", "#4f46e5"),
            },
            status=422,
        )

    customer.display_name = form.cleaned_data["name"]
    customer.email = form.cleaned_data["email"]
    customer.phone = form.cleaned_data.get("phone", "")
    customer.save(update_fields=["display_name", "email", "phone", "updated_at"])

    conversation = customer.conversations.order_by("-last_message_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(customer=customer, last_message_at=timezone.now())

    return render(
        request,
        "widget/partials/chat_panel.html",
        _chat_panel_context(channel, conversation, session_id, site_key),
    )


def _rate_limited(session_id) -> bool:
    key = f"widget_rl:{session_id}"
    count = cache.get(key, 0)
    if count >= RATE_LIMIT_MESSAGES:
        return True
    cache.set(key, count + 1, RATE_LIMIT_SECONDS)
    return False


@csrf_exempt
@xframe_options_exempt
def send_message(request, session_id):
    """HTMX endpoint — form-encoded `message`. Runs the bot engine synchronously
    and returns an HTML fragment of the new bubbles (hx-swap='beforeend')."""
    if request.method != "POST":
        return HttpResponseForbidden("POST only")

    site_key = request.POST.get("site_key", "")
    channel = _get_wordpress_channel(site_key)
    if channel is None:
        return HttpResponseForbidden("Unknown or inactive site key.")

    if _rate_limited(session_id):
        return render(request, "widget/partials/bubbles.html", {
            "pair": False,
            "error": "You're sending messages too quickly — please wait a moment.",
        })

    text = (request.POST.get("message") or "").strip()
    if not text:
        return HttpResponse(status=200)  # Nothing to do; ack silently.

    customer, _ = Customer.objects.get_or_create(
        channel=channel, external_id=session_id, defaults={"display_name": "Website visitor"}
    )
    conversation = customer.conversations.order_by("-last_message_at").first()
    if conversation is None:
        conversation = Conversation.objects.create(customer=customer, last_message_at=timezone.now())

    try:
        reply = handle_inbound_message(conversation, text)
    except Exception:
        reply = Message.objects.create(
            conversation=conversation,
            sender_type=Message.SenderType.BOT,
            content="Sorry, something went wrong on our side. Please try again in a moment.",
        )

    return render(request, "widget/partials/bubbles.html", {
        "pair": True,
        "inbound_text": text,
        "reply": reply,
    })
