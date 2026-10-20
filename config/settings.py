"""
Django settings for CSU-TBI Track.

All secrets and machine-specific values come from environment variables,
loaded from the .env file in the project root (see .env.example).
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    """Read a true/false environment variable."""
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name: str, default: str = "") -> list[str]:
    """Read a comma-separated environment variable as a list."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


# --- Core ---------------------------------------------------------------

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")


# --- Applications -------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",  # needed for the pgvector HNSW index
    # Third party
    "django_q",
    # Project apps
    "accounts",
    "activities",
    "registrations",
    "documents",
    "certificates",
    "reports",
    "startups",
    "assistant",
    "notifications",
]

# TBI_MEMBER is the login model. This must be set before the first `migrate`:
# changing the user model after the auth tables exist breaks Django.
AUTH_USER_MODEL = "accounts.TBIMember"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "accounts.middleware.MustChangePasswordMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- Database (PostgreSQL + pgvector, running in Docker) -----------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "csu_tbi_track"),
        "USER": os.environ.get("POSTGRES_USER", "csu_tbi"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", ""),
        "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Passwords ------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# --- Staff login -------------------------------------------------------------

LOGIN_URL = "accounts:login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "accounts:login"

# Brute-force protection: after this many failed logins for one email (or many more
# from one IP address), that email/IP is locked out for LOGIN_LOCKOUT_MINUTES.
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_MAX_FAILED_ATTEMPTS_PER_IP = 20
LOGIN_LOCKOUT_MINUTES = 15

# The failed-login counters live in Django's cache. A database cache is shared by
# all server processes and survives restarts. Create its table once with:
#   python manage.py createcachetable
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}


# --- Logging -------------------------------------------------------------------
# Security events (lockouts, account changes) go to the "accounts.security" logger.
# Never log passwords.

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"simple": {"format": "{asctime} {levelname} {name}: {message}", "style": "{"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}


# --- Internationalization ----------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True


# --- Static and uploaded files (local storage) ----------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# Uploaded files live in MEDIA_ROOT but are NEVER served from MEDIA_URL (not even in DEBUG):
# documents are downloaded only through documents.views.download_document, which checks the login.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# --- Documents (Step 6) --------------------------------------------------------

DOCUMENT_MAX_UPLOAD_MB = 10  # largest file a member can upload
DOCUMENT_REMINDER_DAYS_BEFORE = 3  # "due soon" reminder: due within this many days
DOCUMENT_REMINDER_HOUR = 8  # the daily reminder task runs at 8:00 AM Manila time


# --- Email ------------------------------------------------------------------
# Console backend for development: emails are printed in the terminal.
# Real SMTP settings are added in Step 9 (certificates).

MAILERS = {
    "default": {
        "BACKEND": "django.core.mail.backends.console.EmailBackend",
    },
}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "CSU-TBI Track <no-reply@localhost>")

# Public address of the site, used to build links in emails (e.g. personal evaluation links).
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")


# --- Background tasks (Django-Q2, database broker, no Redis) ----------------

Q_CLUSTER = {
    "name": "csu_tbi_track",
    "orm": "default",  # use the Django database as the task queue
    "workers": 2,
    "timeout": 300,  # seconds a task may run
    "retry": 360,  # must be larger than timeout
    "catch_up": False,  # don't replay missed schedules after downtime
}


# --- AI (Gemini via google-genai) -------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_CHAT_MODEL = os.environ.get("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# Size of every embedding vector. DOCUMENT_EMBEDDING's VectorField and the
# embedding API call both read this, so it only changes in one place.
EMBEDDING_DIMENSIONS = 768
