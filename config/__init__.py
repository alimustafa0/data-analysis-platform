"""
config package initialiser.

Importing the Celery app here ensures it is loaded whenever Django starts,
which is required for the @shared_task decorator to work correctly in
app-level tasks.py files.
"""

from .celery import app as celery_app

__all__ = ["celery_app"]