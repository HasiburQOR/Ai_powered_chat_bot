from django.contrib import admin

from .models import TravelProfile


@admin.register(TravelProfile)
class TravelProfileAdmin(admin.ModelAdmin):
    list_display = ("profile_number", "full_name", "customer",
                    "is_complete", "travel_date", "updated_at")
    search_fields = ("profile_number", "full_name", "whatsapp_number",
                     "customer__display_name", "customer__email")
    list_filter = ("is_complete",)