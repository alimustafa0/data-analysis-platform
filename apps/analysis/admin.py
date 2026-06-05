"""Admin configuration for the analysis app."""

from django.contrib import admin

from .models import AnalysisResult, Dataset


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    list_display = ("original_filename", "status", "row_count", "column_count", "created_at")
    list_filter = ("status",)
    readonly_fields = ("id", "created_at", "updated_at", "file_size_bytes", "row_count", "column_count")
    search_fields = ("original_filename",)


@admin.register(AnalysisResult)
class AnalysisResultAdmin(admin.ModelAdmin):
    list_display = ("dataset", "row_count", "column_count", "computed_at")
    readonly_fields = ("computed_at",)