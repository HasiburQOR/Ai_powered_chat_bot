from django.urls import path

from . import views

urlpatterns = [
    path("meta/", views.meta_endpoint, name="meta-webhook"),
    # Same endpoint without the trailing slash: Meta doesn't follow the
    # APPEND_SLASH redirect for POSTs, so a callback URL saved without the
    # slash would verify fine yet never deliver a single message.
    path("meta", views.meta_endpoint, name="meta-webhook-noslash"),
]
