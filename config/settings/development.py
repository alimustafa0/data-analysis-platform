"""
Development settings — local machines only.
Never deployed to a production server.
"""

from .base import *  # noqa: F401, F403

DEBUG: bool = True
ALLOWED_HOSTS: list[str] = ["localhost", "127.0.0.1"]

# Uncomment when Django Debug Toolbar is added in Phase 6:
# INSTALLED_APPS += ["debug_toolbar"]
# MIDDLEWARE.insert(0, "debug_toolbar.middleware.DebugToolbarMiddleware")