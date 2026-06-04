"""AppConfig for the core foundation app."""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    """Owns global pages, error handlers, and shared utilities."""

    default_auto_field: str = "django.db.models.BigAutoField"
    name: str = "apps.core"          # Must match the Python import path.
    verbose_name: str = "Core"