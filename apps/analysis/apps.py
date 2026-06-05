"""AppConfig for the analysis app."""

from django.apps import AppConfig


class AnalysisConfig(AppConfig):
    """Owns dataset upload, processing, and results."""

    default_auto_field: str = "django.db.models.BigAutoField"
    name: str = "apps.analysis"
    verbose_name: str = "Analysis"