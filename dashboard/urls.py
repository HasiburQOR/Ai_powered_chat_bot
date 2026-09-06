from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="dashboard-home"),

    path("llm-configs/", views.llm_config_list, name="dashboard-llm-configs"),
    path("llm-configs/new/", views.llm_config_create, name="dashboard-llm-config-create"),
    path("llm-configs/<uuid:pk>/edit/", views.llm_config_update, name="dashboard-llm-config-edit"),
    path("llm-configs/<uuid:pk>/delete/", views.llm_config_delete, name="dashboard-llm-config-delete"),
    path("llm-configs/<uuid:pk>/activate/", views.llm_config_activate, name="dashboard-llm-config-activate"),

    path("knowledge-chunks/", views.chunk_list, name="dashboard-chunks"),
    path("knowledge-chunks/new/", views.chunk_create, name="dashboard-chunk-create"),
    path("knowledge-chunks/<uuid:pk>/edit/", views.chunk_update, name="dashboard-chunk-edit"),
    path("knowledge-chunks/<uuid:pk>/delete/", views.chunk_delete, name="dashboard-chunk-delete"),

    path("rules/", views.rule_list, name="dashboard-rules"),
    path("rules/new/", views.rule_create, name="dashboard-rule-create"),
    path("rules/<uuid:pk>/edit/", views.rule_update, name="dashboard-rule-edit"),
    path("rules/<uuid:pk>/delete/", views.rule_delete, name="dashboard-rule-delete"),

    path("channels/", views.channel_list, name="dashboard-channels"),
    path("channels/new/", views.channel_create, name="dashboard-channel-create"),
    path("channels/<uuid:pk>/edit/", views.channel_update, name="dashboard-channel-edit"),
    path("channels/<uuid:pk>/delete/", views.channel_delete, name="dashboard-channel-deactivate"),

    path("conversations/", views.conversation_list, name="dashboard-conversations"),
    path("conversations/<uuid:pk>/", views.conversation_detail, name="dashboard-conversation-detail"),
    path("conversations/<uuid:pk>/update-status/", views.conversation_update_status, name="dashboard-conversation-update-status"),

    path("settings/", views.bot_settings, name="dashboard-bot-settings"),
]
