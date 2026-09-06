from django.contrib import admin

from .models import Agent


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = ('display_name', 'user', 'is_available')
    list_filter = ('is_available',)
