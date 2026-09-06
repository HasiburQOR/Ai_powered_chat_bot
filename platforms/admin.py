from django.contrib import admin

from .models import Channel


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ('name', 'channel_type', 'is_active', 'created_at')
    list_filter = ('channel_type', 'is_active')
    search_fields = ('name',)
    # credentials are encrypted at rest; editable here as raw JSON
    readonly_fields = ('created_at', 'updated_at')
