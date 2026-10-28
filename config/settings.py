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
                "config.frontend.staff_layout",  # sidebar, top bar counts (staff pages only)
                "config.frontend.public_footer",  # the contact address in the public pages' footer
            ],
            "libraries": {"ui": "config.ui_tags"},  # {% load ui %}: number, percent, relative_time, ...
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
        "HOST": os.environ.get("POSTGRES_HOST", "127.0.0.1"),  # not "localhost": see .env.example
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

# Sessions (staff and startup portal): at most 8 hours after logging in, and they also end when the
# browser is closed (shared office computers).
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# On a real server (DEBUG off) the session and CSRF cookies are sent over HTTPS only. The site must
# then be served over HTTPS, or logging in will not work. (CSRF_COOKIE_HTTPONLY stays off: the
# frontend's JavaScript reads the CSRF token from the cookie.)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# "Forgot password" links for staff (Django's password reset) work for this many seconds, once.
PASSWORD_RESET_TIMEOUT = 60 * 60
# The "forgot password" form is rate limited (config/ratelimit.py, client_ip()): over a limit the
# usual "check your email" page is shown but no email is sent. Every request counts.
PASSWORD_RESET_MAX_REQUESTS_PER_EMAIL = 5
PASSWORD_RESET_MAX_REQUESTS_PER_IP = 20
PASSWORD_RESET_RATE_WINDOW_MINUTES = 60

# Brute-force protection: after this many failed logins for one email (or many more
# from one IP address), that email/IP is locked out for LOGIN_LOCKOUT_MINUTES.
LOGIN_MAX_FAILED_ATTEMPTS = 5
LOGIN_MAX_FAILED_ATTEMPTS_PER_IP = 20
LOGIN_LOCKOUT_MINUTES = 15

# The client's IP (login lockout, registration rate limit) is REMOTE_ADDR unless the site runs
# behind a reverse proxy: then set this to the header the proxy fills in, e.g. X-Forwarded-For
# or X-Real-IP (config/network.py). Leave it empty without a proxy: clients can fake headers.
TRUSTED_PROXY_HEADER = os.environ.get("TRUSTED_PROXY_HEADER", "").strip()

# Public registration (no login) rate limit, counted in the same cache as the login lockout.
# Every POST to the public registration API counts, also refused ones. The per-IP limit is high on
# purpose: many participants register from the same campus network (one public IP address).
REGISTRATION_MAX_ATTEMPTS_PER_IP = 300
REGISTRATION_MAX_ATTEMPTS_PER_EMAIL = 10
REGISTRATION_RATE_WINDOW_MINUTES = 60

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
    # WeasyPrint logs every render step at INFO; keep only its warnings (e.g. a missing image).
    "loggers": {"weasyprint": {"level": "WARNING"}, "fontTools": {"level": "WARNING"}},
}


# --- Internationalization ----------------------------------------------------

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Manila"
USE_I18N = True
USE_TZ = True


# --- Static and uploaded files (local storage) ----------------------------

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Built CSS (python manage.py tailwind build), self-hosted fonts, htmx, Alpine.js, logo and icons.
STATICFILES_DIRS = [BASE_DIR / "static"]

# Uploaded files live in MEDIA_ROOT but are NEVER served from MEDIA_URL (not even in DEBUG):
# documents are downloaded only through documents.views.download_document, which checks the login.
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# --- Documents (Step 6) --------------------------------------------------------

DOCUMENT_MAX_UPLOAD_MB = 10  # largest file a member can upload
DOCUMENT_REMINDER_DAYS_BEFORE = 3  # "due soon" reminder: due within this many days
DOCUMENT_REMINDER_HOUR = 8  # the daily reminder task runs at 8:00 AM Manila time


# --- Startups and Navigatu (Step 7) -------------------------------------------------
# Navigatu is reached ONLY through this provider class (dotted path). The mock reads
# invented data from startups/mock_data/; NavigatuStartupProvider is a stub for later.
STARTUP_PROVIDER = os.environ.get("STARTUP_PROVIDER", "startups.providers.MockStartupProvider")
STARTUP_SYNC_HOUR = 7  # the daily sync runs at 7:00 AM Manila time
# Safety guard: a sync that would deactivate more than this share of the active startups
# (or gets zero records) deactivates nobody. `sync_startups --force` overrides it on purpose.
STARTUP_SYNC_MAX_DEACTIVATE_RATIO = 0.5
STARTUP_PASSWORD_LINK_HOURS = 72  # a "set your password" link expires after this many hours


# --- Certificates (Step 9) ---------------------------------------------------------------
CERTIFICATE_IMAGE_MAX_MB = 5  # largest template background or signature image
# Printed on every certificate ("Given on ... at <this>"). Placeholder until the TBI confirms it.
CERTIFICATE_ORGANIZATION_NAME = "CSU Technology Business Incubator"
# A failed certificate email is retried after these many minutes (then by the daily retry).
CERTIFICATE_EMAIL_RETRY_MINUTES = [5, 30, 120]
# Daily retry of certificate PDFs, issuing and emails, at 8:30 AM Manila time.
CERTIFICATE_RETRY_HOUR = 8
CERTIFICATE_RETRY_MINUTE = 30


# --- Email (Step 9) -------------------------------------------------------------
# SMTP settings come from .env. While DEBUG is on, emails are printed in the terminal unless
# EMAIL_REAL_SENDING=True. Even with real sending, addresses in EMAIL_BLOCKED_DOMAINS (the demo
# data) are never really emailed: they are printed instead (config/mail.py).
EMAIL_REAL_SENDING = env_bool("EMAIL_REAL_SENDING", False)
EMAIL_BLOCKED_DOMAINS = ["demo.example.com"]

SMTP_MAILER = {
    "BACKEND": "config.mail.GuardedSMTPBackend",
    "OPTIONS": {
        "host": os.environ.get("EMAIL_HOST", "localhost"),
        "port": int(os.environ.get("EMAIL_PORT", "587")),
        "username": os.environ.get("EMAIL_HOST_USER", ""),
        "password": os.environ.get("EMAIL_HOST_PASSWORD", ""),
        "use_tls": env_bool("EMAIL_USE_TLS", True),
        "timeout": 30,  # seconds; a hanging server fails the send (it is retried)
    },
}
# Django's console backend, but long links stay on one line and attachments are summarized.
CONSOLE_MAILER = {"BACKEND": "config.mail.ConsoleEmailBackend"}

MAILERS = {
    "default": CONSOLE_MAILER if DEBUG and not EMAIL_REAL_SENDING else SMTP_MAILER,
    "smtp": SMTP_MAILER,  # always real SMTP; only `send_test_email --smtp` uses it
}
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "CSU-TBI Track <no-reply@localhost>")

# Public address of the site, used to build links in emails (e.g. personal evaluation links).
SITE_URL = os.environ.get("SITE_URL", "http://127.0.0.1:8000").rstrip("/")

# The contact address in the footer of the public pages (phase F3). A PLACEHOLDER until the TBI confirms
# its address (docs/TBI_QUESTIONS.md); set the real one in .env.
TBI_CONTACT_EMAIL = os.environ.get("TBI_CONTACT_EMAIL", "tbi.contact@example.com")


# --- Background tasks (Django-Q2, database broker, no Redis) ----------------

Q_CLUSTER = {
    "name": "csu_tbi_track",
    "orm": "default",  # use the Django database as the task queue
    "workers": 2,
    "timeout": 300,  # seconds a task may run
    "retry": 360,  # must be larger than timeout
    "catch_up": False,  # don't replay missed schedules after downtime
    # A task that raised is recorded under "Failed tasks" and NOT re-queued every `retry`
    # seconds forever. (Our tasks catch their own errors; this is the safety net.)
    "ack_failures": True,
}


# --- AI (Gemini via google-genai) -------------------------------------------

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# gemini-2.5-flash is closed to new API users (HTTP 404 since 2026); see ASSUMPTIONS item 20.
# Check what a key can use with: python manage.py check_gemini
GEMINI_CHAT_MODEL = os.environ.get("GEMINI_CHAT_MODEL", "gemini-3.5-flash-lite")
GEMINI_EMBEDDING_MODEL = os.environ.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001")

# Size of every embedding vector. DOCUMENT_EMBEDDING's VectorField and the
# embedding API call both read this, so it only changes in one place.
EMBEDDING_DIMENSIONS = 768


# --- AI assistant (Step 8) ---------------------------------------------------------------
# Starting values, to tune with real TBI documents (ASSUMPTIONS item R3).
ASSISTANT_CHUNK_SIZE = 1200  # characters per chunk (about 300 tokens)
ASSISTANT_CHUNK_OVERLAP = 200  # characters repeated between neighbouring chunks
ASSISTANT_EMBED_BATCH_SIZE = 20  # texts per embedding request
ASSISTANT_MAX_RETRIES = 4  # attempts per Gemini request on rate limits / server errors
ASSISTANT_REQUEST_TIMEOUT_SECONDS = 60  # one Gemini request may take at most this long
# One question in total (all rounds, embeddings and retries). The assistant still answers inside the
# web request, so keep this under the web server's timeout (e.g. gunicorn --timeout 120).
ASSISTANT_QUESTION_TIMEOUT_SECONDS = 90
ASSISTANT_TOP_K = 6  # excerpts retrieved per search
ASSISTANT_MIN_SIMILARITY = 0.55  # cosine similarity below this is "not relevant"
ASSISTANT_HISTORY_MESSAGES = 6  # earlier chat messages sent as context (3 questions + answers)
ASSISTANT_MAX_TOOL_ROUNDS = 4  # function-calling rounds per question
# Never indexed (personal data). Names of DOCUMENT_TYPE rows.
ASSISTANT_EXCLUDED_DOCUMENT_TYPES = ["Attendance Sheet"]
# Daily retry of documents that are approved but not indexed yet (e.g. rate limits).
ASSISTANT_INDEX_HOUR = 7
ASSISTANT_INDEX_MINUTE = 30
