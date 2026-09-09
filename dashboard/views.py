import csv
import io
import json
import uuid as uuid_lib

from accounts.decorators import staff_required
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from conversations.models import Conversation, Message
from knowledge.models import BotSettings, KnowledgeChunk, Rule
from llm.models import LLMConfig
from platforms.models import Channel
from profiles.images import profile_filename, render_profile_card, render_transcript_image
from profiles.models import TravelProfile

from .forms import (
    BotSettingsForm,
    ChannelCredentialsForm,
    ChannelForm,
    ConversationStatusForm,
    KnowledgeChunkForm,
    LLMConfigForm,
    RuleForm,
    TravelProfileForm,
)


def _oob_refresh(tbody_id: str, rows_html: str) -> HttpResponse:
    """OOB table refresh that also auto-closes the open modal.

    The response MUST start with the <tbody> tag — never with a <script>.
    htmx 1.9.12 parses a fragment whose first tag is <script> inside a plain
    <div>, where the HTML parser silently drops <tbody>/<tr>/<td> tags: the
    hx-swap-oob attribute disappears, the table never updates, and the mangled
    row content gets dumped into the modal container (the "CSS breaks after
    adding" bug). Starting with <tbody> makes htmx parse in a table context,
    so the OOB swap updates the table, and the remaining empty primary content
    clears #modal-container — closing the modal with zero scripting.
    """
    return HttpResponse(f'<tbody id="{tbody_id}" hx-swap-oob="innerHTML">{rows_html}</tbody>')


@staff_required
def home(request):
    return render(request, "dashboard/home.html", {
        "llm_count": LLMConfig.objects.count(),
        "chunk_count": KnowledgeChunk.objects.filter(is_active=True).count(),
        "rule_count": Rule.objects.filter(is_active=True).count(),
        "channel_count": Channel.objects.filter(is_active=True).count(),
        "conversation_count": Conversation.objects.count(),
    })


# ---------- LLMConfig ----------

@staff_required
def llm_config_list(request):
    return render(request, "dashboard/llm_config_list.html", {
        "configs": LLMConfig.objects.all().order_by("-is_active", "name"),
    })


@staff_required
def llm_config_create(request):
    form = LLMConfigForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            config = form.save()
            if config.is_active:
                LLMConfig.objects.exclude(pk=config.pk).update(is_active=False)
            rows_html = render(request, "dashboard/partials/llm_config_rows.html", {"configs": LLMConfig.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return _oob_refresh("llm-config-tbody", rows_html)
        return render(request, "dashboard/partials/llm_config_form.html", {"form": form, "config": None}, status=422)
    return render(request, "dashboard/partials/llm_config_form.html", {"form": form, "config": None})


@staff_required
def llm_config_update(request, pk):
    config = get_object_or_404(LLMConfig, pk=pk)
    form = LLMConfigForm(request.POST or None, instance=config)
    if request.method == "POST":
        if form.is_valid():
            config = form.save()
            if config.is_active:
                LLMConfig.objects.exclude(pk=config.pk).update(is_active=False)
            rows_html = render(request, "dashboard/partials/llm_config_rows.html", {"configs": LLMConfig.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return _oob_refresh("llm-config-tbody", rows_html)
        return render(request, "dashboard/partials/llm_config_form.html", {"form": form, "config": config}, status=422)
    return render(request, "dashboard/partials/llm_config_form.html", {"form": form, "config": config})


@staff_required
@require_POST
def llm_config_delete(request, pk):
    get_object_or_404(LLMConfig, pk=pk).delete()
    return HttpResponse("")  # HTMX removes the row client-side


@staff_required
@require_POST
def llm_config_activate(request, pk):
    config = get_object_or_404(LLMConfig, pk=pk)
    LLMConfig.objects.update(is_active=False)
    config.is_active = True
    config.save(update_fields=["is_active"])
    return render(request, "dashboard/partials/llm_config_rows.html", {"configs": LLMConfig.objects.all().order_by("-is_active", "name")})


# ---------- KnowledgeChunk ----------

@staff_required
def chunk_list(request):
    return render(request, "dashboard/chunk_list.html", {"chunks": KnowledgeChunk.objects.all().order_by("-updated_at")})


@staff_required
def chunk_create(request):
    form = KnowledgeChunkForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            rows_html = render(request, "dashboard/partials/chunk_rows.html", {"chunks": KnowledgeChunk.objects.all().order_by("-updated_at")}).content.decode("utf-8")
            return _oob_refresh("chunk-tbody", rows_html)
        return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": None}, status=422)
    return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": None})


@staff_required
def chunk_update(request, pk):
    chunk = get_object_or_404(KnowledgeChunk, pk=pk)
    form = KnowledgeChunkForm(request.POST or None, instance=chunk)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            rows_html = render(request, "dashboard/partials/chunk_rows.html", {"chunks": KnowledgeChunk.objects.all().order_by("-updated_at")}).content.decode("utf-8")
            return _oob_refresh("chunk-tbody", rows_html)
        return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": chunk}, status=422)
    return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": chunk})


@staff_required
@require_POST
def chunk_delete(request, pk):
    get_object_or_404(KnowledgeChunk, pk=pk).delete()
    return HttpResponse("")


# ---------- Rule ----------

@staff_required
def rule_list(request):
    return render(request, "dashboard/rule_list.html", {"rules": Rule.objects.all().order_by("priority", "name")})


@staff_required
def rule_create(request):
    form = RuleForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            rows_html = render(request, "dashboard/partials/rule_rows.html", {"rules": Rule.objects.all().order_by("priority", "name")}).content.decode("utf-8")
            return _oob_refresh("rule-tbody", rows_html)
        return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": None}, status=422)
    return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": None})


@staff_required
def rule_update(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    form = RuleForm(request.POST or None, instance=rule)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            rows_html = render(request, "dashboard/partials/rule_rows.html", {"rules": Rule.objects.all().order_by("priority", "name")}).content.decode("utf-8")
            return _oob_refresh("rule-tbody", rows_html)
        return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": rule}, status=422)
    return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": rule})


@staff_required
@require_POST
def rule_delete(request, pk):
    get_object_or_404(Rule, pk=pk).delete()
    return HttpResponse("")


# ---------- Channel ----------

@staff_required
def channel_list(request):
    return render(request, "dashboard/channel_list.html", {"channels": Channel.objects.all().order_by("-is_active", "name")})


@staff_required
def channel_create(request):
    form = ChannelForm(request.POST or None)
    cred_form = ChannelCredentialsForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid() and cred_form.is_valid():
            channel = form.save(commit=False)
            creds = cred_form.cleaned_data.get("credentials")
            if creds is not None:
                channel.credentials = creds
            channel.save()
            rows_html = render(request, "dashboard/partials/channel_rows.html", {"channels": Channel.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return _oob_refresh("channel-tbody", rows_html)
        return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": None}, status=422)
    return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": None})


@staff_required
def channel_update(request, pk):
    channel = get_object_or_404(Channel, pk=pk)
    form = ChannelForm(request.POST or None, instance=channel)
    cred_form = ChannelCredentialsForm(request.POST or None, initial={"credentials": ""})
    cred_form.fields["credentials"].help_text = "Leave blank to keep existing credentials."
    if request.method == "POST":
        if form.is_valid() and cred_form.is_valid():
            channel = form.save(commit=False)
            creds = cred_form.cleaned_data.get("credentials")
            if creds is not None:
                channel.credentials = creds
            channel.save()
            rows_html = render(request, "dashboard/partials/channel_rows.html", {"channels": Channel.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return _oob_refresh("channel-tbody", rows_html)
        return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": channel}, status=422)
    return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": channel})


@staff_required
@require_POST
def channel_deactivate(request, pk):
    """Soft delete — deactivate the channel but preserve its conversation history."""
    channel = get_object_or_404(Channel, pk=pk)
    channel.is_active = False
    channel.save(update_fields=["is_active"])
    return render(request, "dashboard/partials/channel_row.html", {"channel": channel})


@staff_required
@require_POST
def channel_delete(request, pk):
    """Permanent delete — removes the channel and, via FK cascade, every
    customer, conversation and message that belongs to it. Irreversible."""
    channel = get_object_or_404(Channel, pk=pk)
    channel.delete()
    return HttpResponse("")  # HTMX removes the row client-side


# ---------- Conversations (read-only review + Phase 9 handoff) ----------

def _filtered_conversations(request):
    """Conversations filtered by the dashboard filter bar. Shared by the list
    view and the CSV export so a download always matches what's on screen.

    Supported filters: channel, status, q (matches the visitor details captured
    by the widget's pre-chat form — name, email, phone — plus external ID) and
    has_details (restrict to visitors who provided contact details).
    """
    conversations = Conversation.objects.select_related(
        "customer", "customer__channel", "assigned_agent"
    ).order_by("-last_message_at")
    channel_id = request.GET.get("channel")
    status = request.GET.get("status")
    query = (request.GET.get("q") or "").strip()
    has_details = request.GET.get("has_details")
    if channel_id:
        conversations = conversations.filter(customer__channel_id=channel_id)
    if status:
        conversations = conversations.filter(status=status)
    if query:
        conversations = conversations.filter(
            Q(customer__display_name__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(customer__phone__icontains=query)
            | Q(customer__external_id__icontains=query)
        )
    if has_details == "yes":
        conversations = conversations.exclude(customer__email="")
    elif has_details == "no":
        conversations = conversations.filter(customer__email="")
    return conversations


@staff_required
def conversation_list(request):
    return render(request, "dashboard/conversation_list.html", {
        "conversations": _filtered_conversations(request)[:200],
        "channels": Channel.objects.all(),
        "statuses": Conversation.Status.choices,
        "selected_channel": request.GET.get("channel") or "",
        "selected_status": request.GET.get("status") or "",
        "selected_query": (request.GET.get("q") or "").strip(),
        "selected_has_details": request.GET.get("has_details") or "",
        "querystring": request.GET.urlencode(),
    })


def _csv_safe(value) -> str:
    """Neutralize spreadsheet formula/DDE injection in exported cells.

    Escapes leading =, +, -, @ ONLY when the next character could start a
    formula or DDE payload (letter, bracket, etc.) — plain digits and spaces
    are left alone so phone numbers like "+15550001" are not mangled, while
    attack payloads like "+cmd|'/c calc'!A0" still get a leading apostrophe.
    """
    text = "" if value is None else str(value)
    if text[:1] in ("=", "+", "-", "@") and text[1:2] and not (text[1].isdigit() or text[1].isspace()):
        return "'" + text
    return text


@staff_required
def conversation_export(request):
    """CSV download of the filtered conversations — one row per conversation,
    including the visitor details captured by the widget's pre-chat form and
    the full message transcript."""
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="conversations-{timezone.localtime():%Y%m%d-%H%M}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow([
        "Conversation ID", "Visitor Name", "Email", "Phone", "External ID",
        "Channel", "Status", "Assigned Agent", "Started At", "Last Message At",
        "Message Count", "Transcript",
    ])
    for conversation in _filtered_conversations(request):
        transcript = "\n".join(
            f"{message.get_sender_type_display()}: {message.content}"
            for message in conversation.messages.order_by("created_at")
        )
        writer.writerow([
            _csv_safe(conversation.pk),
            _csv_safe(conversation.customer.display_name),
            _csv_safe(conversation.customer.email),
            _csv_safe(conversation.customer.phone),
            _csv_safe(conversation.customer.external_id),
            _csv_safe(conversation.customer.channel.name),
            _csv_safe(conversation.get_status_display()),
            _csv_safe(conversation.assigned_agent.display_name if conversation.assigned_agent else ""),
            _csv_safe(timezone.localtime(conversation.started_at).strftime("%Y-%m-%d %H:%M:%S")),
            _csv_safe(timezone.localtime(conversation.last_message_at).strftime("%Y-%m-%d %H:%M:%S")),
            conversation.messages.count(),
            _csv_safe(transcript),
        ])
    return response


@staff_required
def conversation_detail(request, pk):
    conversation = get_object_or_404(
        Conversation.objects.select_related("customer", "customer__channel", "assigned_agent"), pk=pk
    )
    form = ConversationStatusForm(initial={"status": conversation.status, "assigned_agent": conversation.assigned_agent})
    return render(request, "dashboard/conversation_detail.html", {
        "conversation": conversation,
        "messages": conversation.messages.all().order_by("created_at"),
        "status_form": form,
    })


@staff_required
@require_POST
def conversation_update_status(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk)
    form = ConversationStatusForm(request.POST)
    if form.is_valid():
        conversation.status = form.cleaned_data["status"]
        conversation.assigned_agent = form.cleaned_data["assigned_agent"]
        conversation.save(update_fields=["status", "assigned_agent"])
    return redirect("dashboard-conversation-detail", pk=pk)


# ---------- BotSettings (singleton) ----------

@staff_required
def bot_settings(request):
    settings_obj = BotSettings.load()
    form = BotSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("dashboard-bot-settings")
    return render(request, "dashboard/bot_settings.html", {"form": form})


# ---------- Travel Profiles ----------

PROFILE_EXPORT_HEADERS = [
    "Profile Number", "Name", "WhatsApp", "Nationality", "Residence Country",
    "GCC Residence Card", "Card Expiry", "Travel Date", "Trip Days", "Adults",
    "Children Ages", "Total Travellers", "Channel", "Complete", "Completed At",
    "Last Updated",
]


def _filtered_profiles(request):
    """Shared query for the list page and both exports (filters stay in sync)."""
    qs = TravelProfile.objects.select_related("customer", "customer__channel")
    channel_id = (request.GET.get("channel") or "").strip()
    if channel_id:
        try:
            uuid_lib.UUID(channel_id)
            qs = qs.filter(customer__channel_id=channel_id)
        except ValueError:
            pass
    completeness = (request.GET.get("complete") or "").strip()
    if completeness == "yes":
        qs = qs.filter(is_complete=True)
    elif completeness == "no":
        qs = qs.filter(is_complete=False)
    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(profile_number__icontains=q)
            | Q(full_name__icontains=q)
            | Q(whatsapp_number__icontains=q)
            | Q(nationality__icontains=q)
            | Q(residence_country__icontains=q)
            | Q(customer__display_name__icontains=q)
            | Q(customer__email__icontains=q)
        )
    return qs


@staff_required
def profile_list(request):
    profiles = _filtered_profiles(request)
    return render(request, "dashboard/profile_list.html", {
        "profiles": profiles[:200],
        "channels": Channel.objects.all(),
        "selected_channel": (request.GET.get("channel") or "").strip(),
        "selected_complete": (request.GET.get("complete") or "").strip(),
        "selected_query": (request.GET.get("q") or "").strip(),
        "querystring": request.GET.urlencode(),
        "total_count": profiles.count(),
    })


def _profile_row(p) -> list:
    card = ""
    if p.gcc_residence_card is True:
        card = "Yes"
    elif p.gcc_residence_card is False:
        card = "No"
    return [
        p.profile_number,
        p.full_name,
        p.whatsapp_number,
        p.nationality,
        p.residence_country,
        card,
        str(p.residence_card_expiry or ""),
        str(p.travel_date or ""),
        p.trip_days if p.trip_days else "",
        p.adults if p.adults else "",
        p.children_ages,
        p.total_travellers or "",
        p.customer.channel.name if p.customer.channel_id else "",
        "Yes" if p.is_complete else "No",
        timezone.localtime(p.completed_at).strftime("%Y-%m-%d %H:%M") if p.completed_at else "",
        timezone.localtime(p.updated_at).strftime("%Y-%m-%d %H:%M"),
    ]


@staff_required
def profile_export_csv(request):
    profiles = _filtered_profiles(request)
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="travel_profiles_{timezone.now():%Y%m%d}.csv"')
    writer = csv.writer(response)
    writer.writerow(PROFILE_EXPORT_HEADERS)
    for profile in profiles:
        writer.writerow([_csv_safe(value) for value in _profile_row(profile)])
    return response


@staff_required
def profile_export_xlsx(request):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    profiles = _filtered_profiles(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Travel Profiles"
    ws.append(PROFILE_EXPORT_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for profile in profiles:
        ws.append([str(value) if value is not None else "" for value in _profile_row(profile)])
    for idx, column in enumerate(ws.columns, start=1):
        longest = max((len(str(c.value)) for c in column if c.value is not None), default=0)
        ws.column_dimensions[get_column_letter(idx)].width = min(longest + 2, 34)
    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="travel_profiles_{timezone.now():%Y%m%d}.xlsx"')
    return response


@staff_required
def profile_detail(request, pk):
    profile = get_object_or_404(
        TravelProfile.objects.select_related("customer", "customer__channel"), pk=pk)
    form = TravelProfileForm(request.POST or None, instance=profile)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("dashboard-profile-detail", pk=profile.pk)
    latest_conversation = profile.customer.conversations.order_by("-last_message_at").first()
    return render(request, "dashboard/profile_detail.html", {
        "profile": profile,
        "form": form,
        "missing_fields": profile.missing_fields(),
        "latest_conversation": latest_conversation,
    })


def _png_response(png_bytes: bytes, filename: str) -> HttpResponse:
    response = HttpResponse(png_bytes, content_type="image/png")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@staff_required
def profile_card_png(request, pk):
    profile = get_object_or_404(
        TravelProfile.objects.select_related("customer", "customer__channel"), pk=pk)
    return _png_response(render_profile_card(profile), profile_filename(profile))


@staff_required
def profile_transcript_png(request, pk):
    profile = get_object_or_404(
        TravelProfile.objects.select_related("customer", "customer__channel"), pk=pk)
    messages = list(
        Message.objects.filter(conversation__customer=profile.customer)
        .select_related("conversation").order_by("created_at")
    )
    name = profile.full_name or profile.customer.display_name or "visitor"
    title = f"{profile.profile_number or 'Travel lead'} — {name}"
    return _png_response(
        render_transcript_image(messages, title=title),
        profile_filename(profile, suffix="_transcript"),
    )
