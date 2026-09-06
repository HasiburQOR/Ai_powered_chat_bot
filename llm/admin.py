from django.contrib import admin

from .models import LLMConfig


@admin.register(LLMConfig)
class LLMConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider', 'model_name', 'is_active', 'updated_at')
    list_filter = ('provider', 'is_active')
    search_fields = ('name', 'model_name')
    readonly_fields = ('created_at', 'updated_at')

    actions = ['deactivate']

    @admin.action(description='Deactivate selected configs')
    def deactivate(self, request, queryset):
        queryset.update(is_active=False)
