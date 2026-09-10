from django.urls import path

from . import views

urlpatterns = [
    path("embed.js", views.embed_js, name="widget-embed-js"),
    path("chat/", views.chat, name="widget-chat"),
    path("chat/<str:session_id>/send/", views.send_message, name="widget-send"),
]
