"""Admin configuration for the analysis app."""

from django.contrib import admin

from .models import AnalysisPipeline, AnalysisResult, Dataset, PipelineStep


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


class PipelineStepInline(admin.TabularInline):
    """Shows all steps nested inside their pipeline in one admin view."""
    model = PipelineStep
    extra = 0
    ordering = ("order",)
    readonly_fields = ("id", "status", "started_at", "completed_at", "duration_seconds", "created_at")
    fields = ("order", "step_type", "status", "started_at", "completed_at", "duration_seconds", "error_message")


@admin.register(AnalysisPipeline)
class AnalysisPipelineAdmin(admin.ModelAdmin):
    list_display = ("name", "dataset", "status", "progress_pct", "created_at")
    list_filter = ("status",)
    readonly_fields = ("id", "created_at", "updated_at", "progress_pct")
    search_fields = ("dataset__original_filename", "name")
    inlines = [PipelineStepInline]