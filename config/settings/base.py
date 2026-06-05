"""
Base settings shared across all environments.

Secrets and infrastructure URLs are read exclusively from .env —
never hard-coded here. Per-environment overrides live in
development.py and production.py.
"""

from pathlib import Path

import environ

# ─── Paths ────────────────────────────────────────────────────────────────────
# base.py is at <root>/config/settings/base.py → three .parent calls = root.
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent

# ─── Environment ──────────────────────────────────────────────────────────────
env = environ.Env(
    DEBUG=(bool, False),  # Defaults to False; production-safe.
)
environ.Env.read_env(str(BASE_DIR / ".env"))

# ─── Security ─────────────────────────────────────────────────────────────────
SECRET_KEY: str = env("SECRET_KEY")
DEBUG: bool = env("DEBUG")
ALLOWED_HOSTS: list[str] = []  # Always overridden per environment.

# ─── Application definition ───────────────────────────────────────────────────
# Three separate lists make it instantly clear what came from where during
# a dependency audit.
DJANGO_APPS: list[str] = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS: list[str] = []  # Populated per phase (HTMX, Celery, etc.)
LOCAL_APPS: list[str] = [
    "apps.core",
    "apps.analysis",
]

INSTALLED_APPS: list[str] = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ─── Middleware ────────────────────────────────────────────────────────────────
MIDDLEWARE: list[str] = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF: str = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],  # Project-level templates live here.
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION: str = "config.wsgi.application"

# ─── Database ─────────────────────────────────────────────────────────────────
# 12-factor app principle: infrastructure URLs live in .env, not in code.
# Swap to DATABASE_URL=postgres://... in production without touching this file.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

# ─── Password validation ──────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ─── Internationalisation ─────────────────────────────────────────────────────
LANGUAGE_CODE: str = "en-us"
TIME_ZONE: str = "UTC"
USE_I18N: bool = True
USE_TZ: bool = True

# ─── Static & media files ─────────────────────────────────────────────────────
STATIC_URL: str = "/static/"
STATICFILES_DIRS: list[Path] = [BASE_DIR / "static"]
STATIC_ROOT: Path = BASE_DIR / "staticfiles"

MEDIA_URL: str = "/media/"
MEDIA_ROOT: Path = BASE_DIR / "media"

# ─── Default primary key ──────────────────────────────────────────────────────
DEFAULT_AUTO_FIELD: str = "django.db.models.BigAutoField"