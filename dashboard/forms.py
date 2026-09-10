from django import forms

from conversations.models import Conversation
from knowledge.models import BotSettings, KnowledgeChunk, Rule
from llm.models import LLMConfig
from platforms.models import Channel

INPUT_CLASSES = (
    "w-full px-3.5 py-2.5 bg-slate-50 border border-slate-300 rounded-xl text-slate-900 "
    "placeholder-slate-400 font-medium text-sm focus:bg-white focus:outline-none "
    "focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all duration-150 shadow-sm"
)
TEXTAREA_CLASSES = (
    "w-full px-3.5 py-2.5 bg-slate-50 border border-slate-300 rounded-xl text-slate-900 "
    "placeholder-slate-400 font-medium text-sm focus:bg-white focus:outline-none "
    "focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 transition-all duration-150 shadow-sm"
)
SELECT_CLASSES = (
    "w-full px-3.5 py-2.5 bg-slate-50 border border-slate-300 rounded-xl text-slate-900 "
    "font-medium text-sm focus:bg-white focus:outline-none focus:ring-2 focus:ring-indigo-500 "
    "focus:border-indigo-500 transition-all duration-150 shadow-sm cursor-pointer"
)
CHECKBOX_CLASSES = (
    "h-5 w-5 text-indigo-600 border-slate-300 rounded focus:ring-indigo-500 cursor-pointer shadow-sm"
)


class StyledFormMixin:
    """Applies vibrant, high-visibility input styling to all Django form fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                widget.attrs["class"] = CHECKBOX_CLASSES
            elif isinstance(widget, forms.Select):
                existing_class = widget.attrs.get("class", "")
                widget.attrs["class"] = f"{SELECT_CLASSES} {existing_class}".strip()
            elif isinstance(widget, forms.Textarea):
                existing_class = widget.attrs.get("class", "")
                widget.attrs["class"] = f"{TEXTAREA_CLASSES} {existing_class}".strip()
            else:
                existing_class = widget.attrs.get("class", "")
                widget.attrs["class"] = f"{INPUT_CLASSES} {existing_class}".strip()


class LLMConfigForm(StyledFormMixin, forms.ModelForm):
    # Never round-trip the real key into the form; blank on edit = "leave unchanged".
    api_key = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs={"placeholder": "sk-••••••••••••"})
    )

    class Meta:
        model = LLMConfig
        fields = ["name", "provider", "model_name", "api_base_url", "api_key",
                  "system_prompt", "temperature", "max_tokens", "is_active"]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            key = self.instance.api_key or ""
            self.fields["api_key"].initial = ""
            self.fields["api_key"].help_text = (
                f"Currently set: {key[:3]}••••{key[-4:] if len(key) > 8 else ''} — leave blank to keep existing key."
            )

    def save(self, commit=True):
        obj = super().save(commit=False)
        api_key = self.cleaned_data.get("api_key")
        if self.instance.pk and not api_key:
            # Keep the existing encrypted value.
            obj.api_key = type(self.instance).objects.get(pk=self.instance.pk).api_key
        if commit:
            obj.save()
        return obj


class KnowledgeChunkForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = KnowledgeChunk
        fields = ["title", "category", "content", "is_active"]
        widgets = {
            "content": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {"content": "Saved changes automatically re-embed this chunk in the background."}


class RuleForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Rule
        fields = ["name", "trigger_keywords", "response_text", "short_circuits_llm", "priority", "is_active"]
        widgets = {
            "trigger_keywords": forms.Textarea(attrs={"rows": 2, "placeholder": '["hello", "pricing"]'}),
            "response_text": forms.Textarea(attrs={"rows": 3}),
        }


class ChannelForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = Channel
        fields = ["name", "channel_type", "is_active"]
        help_texts = {
            "channel_type": "Credentials (tokens, site_key, etc.) can be configured after creation."
        }


class ChannelCredentialsForm(StyledFormMixin, forms.Form):
    """Separate JSON editor for Channel.credentials (encrypted at rest)."""
    credentials = forms.CharField(widget=forms.Textarea(attrs={"rows": 6, "placeholder": '{\n  "page_id": "...",\n  "page_access_token": "..."\n}'}), required=False)

    def clean_credentials(self):
        import json

        raw = self.cleaned_data.get("credentials", "").strip()
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise forms.ValidationError("Credentials must be valid JSON.")


class BotSettingsForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = BotSettings
        fields = [
            "max_context_messages", "fallback_message", "memory_summary_trigger_count",
            "profile_collection_enabled", "profile_intro_message",
        ]
        widgets = {
            "fallback_message": forms.Textarea(attrs={"rows": 3}),
            "profile_intro_message": forms.Textarea(attrs={"rows": 5}),
        }


class ConversationStatusForm(StyledFormMixin, forms.Form):
    """Phase 9: staff handoff — update status / assigned agent from the dashboard."""
    status = forms.ChoiceField(choices=Conversation.Status.choices)
    assigned_agent = forms.ModelChoiceField(
        Conversation.assigned_agent.field.remote_field.model.objects.all(), required=False
    )

