"""URL configuration for the analysis app."""

from django.urls import path

from . import views

app_name: str = "analysis"

urlpatterns = [
    path("upload/", views.upload, name="upload"),
]