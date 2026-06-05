"""
Celery application factory.

This module is imported by the worker process and by Django via
config/__init__.py. Keeping it in the config package means Celery
shares the same Django settings without any duplication.
"""

import os

from celery import Celery

# Tell Celery which Django settings module to use before anything else loads.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app: Celery = Celery("data_analysis_platform")

# Namespace='CELERY' means all Celery-related settings in settings.py
# must be prefixed with CELERY_ — avoids name collisions.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Auto-discover tasks.py in every installed app — no manual registration.
app.autodiscover_tasks()