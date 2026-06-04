"""URL configuration for the core app."""

from django.urls import path

from . import views

app_name: str = "core"  # Namespace prevents URL name collisions across apps.

urlpatterns = [
    path("", views.home, name="home"),
]