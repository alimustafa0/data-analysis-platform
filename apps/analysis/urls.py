"""URL configuration for the analysis app."""

from django.urls import path

from . import views

app_name: str = "analysis"

urlpatterns = [
    path("", views.dataset_list, name="list"),
    path("upload/", views.upload, name="upload"),
    path("<uuid:pk>/", views.result, name="result"),
    path("<uuid:dataset_pk>/pipeline/new/", views.pipeline_create, name="pipeline_create"),
    path("<uuid:dataset_pk>/pipeline/submit/", views.pipeline_submit, name="pipeline_submit"),
    path("pipeline/<uuid:pk>/", views.pipeline_status, name="pipeline_status"),
    path("pipeline/<uuid:pk>/download-model/", views.model_download, name="model_download"),
]