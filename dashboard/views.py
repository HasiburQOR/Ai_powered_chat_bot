import json

from django.contrib import messages
from accounts.decorators import staff_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from conversations.models import Conversation
from knowledge.models import BotSettings, KnowledgeChunk, Rule
from llm.models import LLMConfig
from platforms.models import Channel

from .forms import (
    BotSettingsForm,
    ChannelCredentialsForm,
    ChannelForm,
    ConversationStatusForm,
    KnowledgeChunkForm,
    LLMConfigForm,
    RuleForm,
)


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
            messages.success(request, f"Created {config.name}.")
            rows_html = render(request, "dashboard/partials/llm_config_rows.html", {"configs": LLMConfig.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="llm-config-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
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
            messages.success(request, f"Updated {config.name}.")
            rows_html = render(request, "dashboard/partials/llm_config_rows.html", {"configs": LLMConfig.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="llm-config-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
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
    messages.success(request, f"{config.name} is now the active LLM.")
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
            messages.success(request, "Knowledge chunk created (embedding queued).")
            rows_html = render(request, "dashboard/partials/chunk_rows.html", {"chunks": KnowledgeChunk.objects.all().order_by("-updated_at")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="chunk-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
        return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": None}, status=422)
    return render(request, "dashboard/partials/chunk_form.html", {"form": form, "chunk": None})


@staff_required
def chunk_update(request, pk):
    chunk = get_object_or_404(KnowledgeChunk, pk=pk)
    form = KnowledgeChunkForm(request.POST or None, instance=chunk)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Chunk updated (re-embedding queued).")
            rows_html = render(request, "dashboard/partials/chunk_rows.html", {"chunks": KnowledgeChunk.objects.all().order_by("-updated_at")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="chunk-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
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
            messages.success(request, "Rule created.")
            rows_html = render(request, "dashboard/partials/rule_rows.html", {"rules": Rule.objects.all().order_by("priority", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="rule-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
        return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": None}, status=422)
    return render(request, "dashboard/partials/rule_form.html", {"form": form, "rule": None})


@staff_required
def rule_update(request, pk):
    rule = get_object_or_404(Rule, pk=pk)
    form = RuleForm(request.POST or None, instance=rule)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "Rule updated.")
            rows_html = render(request, "dashboard/partials/rule_rows.html", {"rules": Rule.objects.all().order_by("priority", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="rule-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
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
            messages.success(request, f"Channel {channel.name} created.")
            rows_html = render(request, "dashboard/partials/channel_rows.html", {"channels": Channel.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="channel-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
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
            messages.success(request, f"Channel {channel.name} updated.")
            rows_html = render(request, "dashboard/partials/channel_rows.html", {"channels": Channel.objects.all().order_by("-is_active", "name")}).content.decode("utf-8")
            return HttpResponse(
                f'<script>document.getElementById("modal-overlay")?.remove();</script>'
                f'<tbody id="channel-tbody" hx-swap-oob="innerHTML">{rows_html}</tbody>'
            )
        return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": channel}, status=422)
    return render(request, "dashboard/partials/channel_form.html", {"form": form, "cred_form": cred_form, "channel": channel})


@staff_required
@require_POST
def channel_delete(request, pk):
    """Soft delete per the schema — deactivate instead of removing the row."""
    channel = get_object_or_404(Channel, pk=pk)
    channel.is_active = False
    channel.save(update_fields=["is_active"])
    return render(request, "dashboard/partials/channel_row.html", {"channel": channel})


# ---------- Conversations (read-only review + Phase 9 handoff) ----------

@staff_required
def conversation_list(request):
    conversations = Conversation.objects.select_related("customer", "customer__channel", "assigned_agent").order_by("-last_message_at")
    channel_id = request.GET.get("channel")
    status = request.GET.get("status")
    if channel_id:
        conversations = conversations.filter(customer__channel_id=channel_id)
    if status:
        conversations = conversations.filter(status=status)
    return render(request, "dashboard/conversation_list.html", {
        "conversations": conversations[:200],
        "channels": Channel.objects.all(),
        "statuses": Conversation.Status.choices,
        "selected_channel": channel_id or "",
        "selected_status": status or "",
    })


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
        conversation.save(update_fields=["status", "assigned_agent", "updated_at"])
        messages.success(request, "Conversation updated.")
    return redirect("dashboard-conversation-detail", pk=pk)


# ---------- BotSettings (singleton) ----------

@staff_required
def bot_settings(request):
    settings_obj = BotSettings.load()
    form = BotSettingsForm(request.POST or None, instance=settings_obj)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Bot settings saved.")
        return redirect("dashboard-bot-settings")
    return render(request, "dashboard/bot_settings.html", {"form": form})
