from django.contrib import admin

from .models import BotSettings, KnowledgeChunk, Rule


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ('title', 'category', 'is_active', 'created_at')
    list_filter = ('is_active', 'category')
    search_fields = ('title', 'content')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'priority', 'short_circuits_llm', 'is_active')
    list_filter = ('is_active', 'short_circuits_llm')
    search_fields = ('name', 'response_text')
    readonly_fields = ('created_at', 'updated_at')


@admin.register(BotSettings)
class BotSettingsAdmin(admin.ModelAdmin):
    list_display = ('id', 'max_context_messages', 'memory_summary_trigger_count', 'updated_at')

    def has_add_permission(self, request):
        # Singleton — only one row ever exists.
        return not BotSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
