"""Admin configuration for the analysis app."""

from django.contrib import admin

from .models import Dataset


@admin.register(Dataset)
class DatasetAdmin(admin.ModelAdmin):
    """Admin view for uploaded datasets."""

    list_display = ("original_filename", "status", "row_count", "column_count", "created_at")
    list_filter = ("status",)
    readonly_fields = ("id", "created_at", "updated_at", "file_size_bytes", "row_count", "column_count")
    search_fields = ("original_filename",)