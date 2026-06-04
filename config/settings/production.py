"""
Production settings — loaded by the web server on deployment.
Never used on a development machine.
"""

from .base import *  # noqa: F401, F403

DEBUG: bool = False
ALLOWED_HOSTS: list[str] = env.list("ALLOWED_HOSTS", default=[])  # noqa: F405

# ─── Security hardening — uncommented fully in Phase 7 ────────────────────────
# SECURE_SSL_REDIRECT = True
# SESSION_COOKIE_SECURE = True
# CSRF_COOKIE_SECURE = True
# SECURE_HSTS_SECONDS = 31_536_000