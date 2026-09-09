from django.contrib import admin

from .models import Conversation, Customer, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    readonly_fields = ('sender_type', 'content', 'raw_payload', 'created_at')
    can_delete = False


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'email', 'phone', 'channel', 'external_id', 'created_at')
    list_filter = ('channel',)
    search_fields = ('display_name', 'external_id', 'email', 'phone')
    readonly_fields = ('created_at', 'updated_at')


class ConversationInline(admin.TabularInline):
    model = Conversation
    extra = 0
    readonly_fields = ('started_at', 'last_message_at')
    can_delete = False
    show_change_link = True


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ('customer', 'status', 'assigned_agent', 'started_at', 'last_message_at')
    list_filter = ('status',)
    search_fields = ('customer__display_name', 'customer__external_id')
    readonly_fields = ('started_at', 'last_message_at')
    inlines = [MessageInline]

    def has_add_permission(self, request):
        return False  # Conversations are created automatically from inbound messages


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('conversation', 'sender_type', 'created_at', 'short_content')
    list_filter = ('sender_type',)
    search_fields = ('content',)
    readonly_fields = ('conversation', 'sender_type', 'content', 'raw_payload', 'created_at')

    def short_content(self, obj):
        return obj.content[:80]

    def has_add_permission(self, request):
        return False  # Append-only from the application's perspective
