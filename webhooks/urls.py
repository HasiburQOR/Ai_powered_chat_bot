from django.urls import path

from . import views

urlpatterns = [
    path("meta/", views.meta_endpoint, name="meta-webhook"),
]
