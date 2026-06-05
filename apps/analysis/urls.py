"""URL configuration for the analysis app."""

from django.urls import path

from . import views

app_name: str = "analysis"

urlpatterns = [
    path("", views.dataset_list, name="list"),
    path("upload/", views.upload, name="upload"),
    path("<uuid:pk>/", views.result, name="result"),
]