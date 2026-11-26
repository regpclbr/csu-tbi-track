"""
Django settings for CSU-TBI Track.

All secrets and machine-specific values come from environment variables,
loaded from the .env file in the project root (see .env.example).
"""

import os
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    """Read a true/false environment variable (missing or empty = the default)."""
    value = os.environ.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"} if value else default


def env_int(name: str, default: int) -> int:
    """Read a whole-number environment variable (empty = the default)."""
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


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
    "startups",  # kept ONLY for its migrations: the portal and STARTUP were removed (ASSUMPTIONS 458)
    "assistant",
    "notifications",
    "approvals",  # work submitted to the Director, the "For your approval" inbox (ASSUMPTIONS 437)
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
    "config.headers.NoIndexMiddleware",  # X-Robots-Tag: noindex on every response (never in a search engine)
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
                "config.frontend.review_label",  # the "Review version" label (REVIEW_VERSION_LABEL, empty = none)
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

# Sessions (staff): at most 8 hours after logging in, and they also end when the
# browser is closed (shared office computers).
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
# On a real server (DEBUG off) the session and CSRF cookies are sent over HTTPS only. The site must
# then be served over HTTPS, or logging in will not work. (CSRF_COOKIE_HTTPONLY stays off: the
# frontend's JavaScript reads the CSRF token from the cookie.)
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# --- Behind an HTTPS reverse proxy (the hosted review version: Caddy, docs/DEPLOY.md) -------------
# Caddy ends HTTPS and passes the request on over plain HTTP inside the server, with X-Forwarded-Proto: https.
# Only with BEHIND_HTTPS_PROXY=True does Django believe that header (anyone can send it to a server without a proxy).
BEHIND_HTTPS_PROXY = env_bool("BEHIND_HTTPS_PROXY", False)
if BEHIND_HTTPS_PROXY:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    # Browsers remember "HTTPS only" for this many seconds. Short on purpose and without includeSubDomains: the
    # Azure name (<name>.<region>.cloudapp.azure.com) belongs to a shared domain (ASSUMPTIONS 391).
    SECURE_HSTS_SECONDS = env_int("SECURE_HSTS_SECONDS", 3600)
SECURE_CONTENT_TYPE_NOSNIFF = True

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
# QR attendance check-in (ASSUMPTIONS 434): /checkin/<id>/ opens this many minutes before start_time on every activity
# day (also on day 1 while the activity is still Scheduled) and closes at end_time (Manila).
PARTICIPANT_CHECKIN_EARLY_MINUTES = env_int("PARTICIPANT_CHECKIN_EARLY_MINUTES", 60)
# ASSUMPTIONS 454: the QR check-in closes this many minutes AFTER the start time on each day (late participants); after
# that the page says to ask the staff, who mark attendance by hand (or add a walk-in, ASSUMPTIONS 455).
PARTICIPANT_CHECKIN_LATE_MINUTES = env_int("PARTICIPANT_CHECKIN_LATE_MINUTES", 60)
# Rate limit of the public check-in page (every POST counts: email, signature and walk-in steps): per IP address (a
# whole venue shares one network) and per typed email, in CHECKIN_RATE_WINDOW_MINUTES.
CHECKIN_MAX_ATTEMPTS_PER_IP = 600
CHECKIN_MAX_ATTEMPTS_PER_EMAIL = 10
CHECKIN_RATE_WINDOW_MINUTES = 60
# Participant signatures (ASSUMPTIONS 434): an uploaded image at most this size (JPG/PNG, content checked), a drawn one
# (a PNG from the canvas) at most PARTICIPANT_SIGNATURE_DRAWN_MAX_KB. Both are re-encoded by Pillow to a PNG that fits
# in PARTICIPANT_SIGNATURE_MAX_PIXELS (width, height).
PARTICIPANT_SIGNATURE_MAX_KB = 2048
PARTICIPANT_SIGNATURE_DRAWN_MAX_KB = 500
PARTICIPANT_SIGNATURE_MAX_PIXELS = (900, 300)
# Signature images are deleted at the latest this many days after the activity's end date (ASSUMPTIONS 434); earlier
# once the Attendance Sheet, the Confirmation of Attendance and the Activity Report are uploaded and the newest two
# forms are newer than the last attendance or registration change. Checked daily at this Manila time.
PARTICIPANT_SIGNATURE_RETENTION_DAYS = 365
SIGNATURE_PURGE_HOUR = 7
SIGNATURE_PURGE_MINUTE = 45
# Participant photos with the evaluation (ASSUMPTIONS 435): at most this many files per evaluation, each at most this many
# MB (JPG / PNG / PDF, content checked like document uploads). Photos are re-encoded by Pillow (EXIF and GPS dropped) to
# fit EVALUATION_PHOTO_MAX_SIDE pixels on the long side; PDFs are stored as they are.
EVALUATION_PHOTO_MAX_FILES = 5
EVALUATION_PHOTO_MAX_MB = 10
EVALUATION_PHOTO_MAX_SIDE = 2400

# The general evaluation link (/evaluation/<id>/): "email me my evaluation link" requests, counted in the
# same cache. Over a limit the page shows the same neutral message and sends nothing (ASSUMPTIONS 335).
EVALUATION_REQUEST_MAX_PER_IP = 300  # high on purpose: a whole campus can share one public IP address
EVALUATION_REQUEST_MAX_PER_EMAIL = 3
EVALUATION_REQUEST_RATE_WINDOW_MINUTES = 60

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
    # Personal links (evaluation, cancel registration, password reset) carry a token in the URL; Django logs the path
    # of every 404/400 ("Not Found: /evaluate/<token>/"), so the token is replaced by "<hidden>" (ASSUMPTIONS 391).
    "filters": {"hide_tokens": {"()": "config.logfilters.HideTokensFilter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "simple", "filters": ["hide_tokens"]}},
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
# Types not offered on the New activity form (ASSUMPTIONS 327). Types due after the end date (Attendance Sheet) are left
# out anyway; these have no due rule but only make sense afterwards (the Accomplishment Report is optional since
# ASSUMPTIONS 386, so it has no due rule any more).
DOCUMENT_TYPES_AFTER_ACTIVITY = ["Accomplishment Report", "Certificate", "Evaluation Form"]
# Required uploads on the New activity form (ASSUMPTIONS 405, NAVIGATU): they exist when the activity is created. Only
# active, required types with these names count (documents.services.creation_required_types).
DOCUMENT_TYPES_REQUIRED_AT_CREATION = ["Activity Design", "Request Letter"]
# Participant mix (ASSUMPTIONS 424, NAVIGATU guideline): at most 30 % Faculty and at least 70 % Incubatees. Until NAVIGATU
# says how MSMEs count, they count WITH the Incubatees (owner decision 2026-11-22). Names of PARTICIPANT_CATEGORY rows
# (the seeded names, which cannot be renamed). Only a warning: registration is never blocked.
PARTICIPANT_MIX_FACULTY = "Faculty"
PARTICIPANT_MIX_FACULTY_MAX_PERCENT = 30
PARTICIPANT_MIX_INCUBATEES = ["Incubatees", "MSMEs"]  # counted together for the minimum
PARTICIPANT_MIX_INCUBATEES_MIN_PERCENT = 70
# Payment documents for the Supply Office (ASSUMPTIONS 425, NAVIGATU UAT): required types needed after the activity
# (due end date + 5, set in their DOCUMENT_TYPE rows). The Documents tab groups them under "For payment (Supply
# Office)", they count in Needs attention, reminders and overdue numbers only once the activity is Conducted, and
# "Download payment packet" zips their Approved files. Names of DOCUMENT_TYPE rows.
# "Confirmation of Attendance" joined them on 2026-11-24 (ASSUMPTIONS 434): attached to the Activity Report after the
# Attendance, generated by the system from the pre-registrations (registrations.attendance_forms).
CONFIRMATION_OF_ATTENDANCE = "Confirmation of Attendance"
DOCUMENT_TYPES_FOR_PAYMENT = ["Activity Report", "Attendance Sheet", "Confirmation of Attendance", "Event Photos"]
# Of those, the types that are new in item 425 apply only to activities ENDING on or after PAYMENT_DOCUMENTS_FROM, so
# older activities never show them as missing or overdue (owner decision 2026-11-22). The Attendance Sheet was
# already required for every activity and stays so.
PAYMENT_DOCUMENTS_FROM = date.fromisoformat(os.environ.get("PAYMENT_DOCUMENTS_FROM", "2026-11-22"))
# The Confirmation of Attendance (ASSUMPTIONS 434) is required only for activities ending on or after this date.
CONFIRMATION_DOCUMENTS_FROM = date.fromisoformat(os.environ.get("CONFIRMATION_DOCUMENTS_FROM", "2026-11-25"))
# Required types that apply only to activities ENDING on or after a date: name -> date (documents.services.type_applies
# and its SQL twin reports.metrics._applies). Every other required type applies to every activity.
DOCUMENT_TYPES_REQUIRED_FROM = {
    "Activity Report": PAYMENT_DOCUMENTS_FROM,
    "Event Photos": PAYMENT_DOCUMENTS_FROM,
    CONFIRMATION_OF_ATTENDANCE: CONFIRMATION_DOCUMENTS_FROM,
}
DOCUMENT_TYPES_FROM_PAYMENT_DATE = [name for name, since in DOCUMENT_TYPES_REQUIRED_FROM.items() if since == PAYMENT_DOCUMENTS_FROM]
# Types whose files must be pictures (JPG or PNG); several may be uploaded (ASSUMPTIONS 425).
DOCUMENT_TYPES_IMAGES_ONLY = ["Event Photos"]


# --- Activities: automatic statuses and evaluation links (ASSUMPTIONS 346, 348) -------------
# How often the Django-Q2 task moves Scheduled -> Ongoing -> Conducted and sends the first evaluation links.
ACTIVITY_STATUS_INTERVAL_MINUTES = 5
# The first evaluation links also go out while the activity is still Ongoing, from this many hours before its end
# (never before the evaluation opens at 00:00 on the end date; ASSUMPTIONS 419, NAVIGATU UAT).
EVALUATION_LINK_HOURS_BEFORE_END = env_int("EVALUATION_LINK_HOURS_BEFORE_END", 3)
# "Needs attention": a Draft whose start date is this many days away (or past) is flagged.
ATTENTION_DRAFT_DAYS_BEFORE = 7


# --- Startups and Navigatu: removed (ASSUMPTIONS 458) -----------------------------------
# The startup portal, the Navigatu sync (StartupProvider) and the STARTUP table were removed on 2026-11-26 before the
# user testing; they are a Chapter 5 recommendation. No STARTUP_* setting is read any more.


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
# Forms posted over HTTPS must come from the public address (Django's CSRF Origin check).
CSRF_TRUSTED_ORIGINS = [SITE_URL] if SITE_URL.startswith("https://") else []

# A small label on the staff pages and the sign-in page, e.g. "Review version" on the hosted client review copy
# (ASSUMPTIONS 392). Empty = no label (development).
REVIEW_VERSION_LABEL = os.environ.get("REVIEW_VERSION_LABEL", "").strip()

# The contact address in the footer of the public pages (phase F3). A PLACEHOLDER until the TBI confirms
# its address (open question, CLAUDE.md); set the real one in .env.
TBI_CONTACT_EMAIL = os.environ.get("TBI_CONTACT_EMAIL", "tbi.contact@example.com")


# --- Background tasks (Django-Q2, database broker, no Redis) ----------------

Q_CLUSTER = {
    "name": "csu_tbi_track",
    "orm": "default",  # use the Django database as the task queue
    # One worker on the small review server (about 1 GB of memory, docs/DEPLOY.md); 2 by default.
    "workers": env_int("Q_CLUSTER_WORKERS", 2),
    # A worker process is replaced after this many tasks, so memory used by PDF renders is given back.
    "recycle": 50,
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
# Reports -> New report, "Describe the report you want" (ASSUMPTIONS 384): Gemini only reads the request.
REPORT_FORM_FILL_MAX_CHARS = 500  # longest request accepted
REPORT_FORM_FILL_TIMEOUT_SECONDS = 30  # the whole "Fill in the form" call
REPORT_FORM_FILL_MAX_PER_HOUR = 30  # per member (config.ratelimit)
ASSISTANT_TOP_K = 6  # excerpts retrieved per search
ASSISTANT_MIN_SIMILARITY = 0.55  # cosine similarity below this is "not relevant"
ASSISTANT_HISTORY_MESSAGES = 6  # earlier chat messages sent as context (3 questions + answers)
ASSISTANT_MAX_TOOL_ROUNDS = 4  # function-calling rounds per question
# Never indexed. Names of DOCUMENT_TYPE rows. Empty since 2026-11-24 (owner decision, ASSUMPTIONS 434): the assistant
# reads every approved file, names included (the Attendance Sheet and the Confirmation of Attendance are part of the
# Activity Report); signature IMAGES are never text, so they never reach Gemini.
ASSISTANT_EXCLUDED_DOCUMENT_TYPES: list[str] = []
# Reading scans with Gemini (OCR, ASSUMPTIONS 438): a PDF without text or a JPG / PNG of a document is sent to Gemini to
# be read once (on upload, replace, approval or the tick; never by the daily backfill), and the text is kept with the
# chunks. Costs tokens (about 260-1,100 per page + the text read): see ASSUMPTIONS 438. On the free tier Google may use
# the data: a paid API key is required before real participant data is read (open question, CLAUDE.md).
ASSISTANT_OCR_ENABLED = os.environ.get("ASSISTANT_OCR_ENABLED", "true").strip().lower() == "true"
ASSISTANT_OCR_MAX_PAGES = 30  # a longer scanned PDF is not read (cost and time)
ASSISTANT_OCR_TIMEOUT_SECONDS = 180  # one OCR request (a background task, never a page)
# Pictures of people, not documents: never read with OCR (names of DOCUMENT_TYPE rows).
ASSISTANT_OCR_EXCLUDED_TYPES: list[str] = ["Event Photos"]
# Scans of these types show handwriting and participants' signatures: read only after a member ticks
# "Read this scan with AI" for that file (DOCUMENT.ai_scan_allowed_by / _at; owner decision 2026-11-25).
ASSISTANT_SCAN_TICK_TYPES: list[str] = ["Attendance Sheet", "Confirmation of Attendance"]
# --- Report generator (ASSUMPTIONS 442-445) ---
REPORT_GENERATOR_MAX_ATTACHMENTS = 10  # files per draft (each at most DOCUMENT_MAX_UPLOAD_MB)
REPORT_GENERATOR_MESSAGE_MAX = 2000  # characters per request, like the AI Assistant
REPORT_GENERATOR_FILE_CHARS = 8000  # characters of one file sent to the AI
REPORT_GENERATOR_TOTAL_FILE_CHARS = 60000  # characters of all files together (the rest is left out and said so)
REPORT_GENERATOR_TIMEOUT_SECONDS = 180  # writing one version (a background task, never a page)
REPORT_GENERATOR_PHOTOS_PER_PAGE = 6
REPORT_GENERATOR_PHOTO_MAX_SIDE = 1600  # photos are shrunk to this before they go into a PDF / DOCX
# Placeholders until NAVIGATU gives the letterhead (open question, CLAUDE.md).
REPORT_IMPLEMENTING_UNIT = "CSU Technology Business Incubator"
ACTIVITY_REPORT_FORM_CODE = "F-COR-001 Rev. 3 10/06/2023"
ACTIVITY_REPORT_TYPE = "Activity Report"  # the DOCUMENT_TYPE a saved Activity Report becomes (an approved copy)
EVENT_PHOTOS_TYPE = "Event Photos"
# Paper sheets (ASSUMPTIONS 443): an office is mapped to a PARTICIPANT_CATEGORY by these words (case ignored, first
# match wins). DTI and partner agencies count as MSMEs (confirmed 2026-11-26, ASSUMPTIONS 443). Staff check
# and correct every row before the numbers are used.
PAPER_SHEET_CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Faculty", ["caraga state university", "csu", "faculty", "professor", "instructor", "college of"]),
    ("Incubatees", ["incubatee", "tbi incubat", "startup", "start-up"]),
    ("MSMEs", ["dti", "department of trade", "negosyo", "msme", "sme", "enterprise", "trading", "store", "partner"]),
]
# Daily retry of documents that are approved but not indexed yet (e.g. rate limits).
ASSISTANT_INDEX_HOUR = 7
ASSISTANT_INDEX_MINUTE = 30
