"""Admin Console "System status" (phase F6): read-only checks of what the app needs to work in the background.

Each check returns a StatusItem: ok (green), warning (amber, with a one-line fix) or info (blue: development mode).
Nothing here changes data, sends an email or calls Gemini: the Gemini check only says whether GEMINI_API_KEY is set
(the real test is `python manage.py check_gemini`). Secrets (the SMTP password and user, the Gemini key) are never
read into an item. The PDF check renders one line in memory through certificates.pdf.get_renderer() (the fake
renderer in tests); the page loads it separately so the rest appears at once.
"""

from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings

from config.mail import prints_emails

OK, WARNING, INFO = "ok", "warning", "info"
PDF_TEST_HTML = "<p>CSU-TBI Track status check</p>"


@dataclass(frozen=True)
class StatusItem:
    key: str
    label: str
    tone: str  # OK | WARNING | INFO
    summary: str
    fix: str = ""  # one line: what to do (amber items)
    details: list[tuple[str, str]] = field(default_factory=list)  # (label, value) pairs; never a secret
    rows: list[dict] = field(default_factory=list)  # the schedules table


def qcluster_status() -> StatusItem:
    """Running when Django-Q's sentinel has saved its status lately (the database cache, shared by every process)."""
    from django_q.status import Stat

    try:
        clusters = Stat.get_all()
    except Exception:  # the cache table missing or unreadable: same answer as "not running"
        clusters = []
    if clusters:
        workers = sum(len(cluster.workers or []) for cluster in clusters)
        return StatusItem("qcluster", "Background worker (qcluster)", OK, f"Running: {len(clusters)} cluster{'s' if len(clusters) != 1 else ''}, {workers} worker{'s' if workers != 1 else ''}.")
    return StatusItem(
        "qcluster", "Background worker (qcluster)", WARNING,
        "Not running: reminders, indexing, reports and certificate emails wait until it starts.",
        fix="Open a new terminal and run: python manage.py qcluster",
    )


def schedule_status() -> StatusItem:
    """The daily tasks of config.schedules.SCHEDULES: next run and the last run's result."""
    from django_q.models import Schedule, Task

    from config.schedules import SCHEDULES

    rows, missing, failed = [], 0, 0
    saved = {schedule.name: schedule for schedule in Schedule.objects.filter(name__in=[s.name for s in SCHEDULES])}
    last_tasks = {task.id: task for task in Task.objects.filter(id__in=[s.task for s in saved.values() if s.task])}
    for daily in SCHEDULES:
        schedule = saved.get(daily.name)
        task = last_tasks.get(schedule.task) if schedule and schedule.task else None
        if schedule is None:
            missing += 1
            last = "Not set up"
        elif task is None:
            last = "Not run yet"
        elif task.success:
            last = "Succeeded"
        else:
            failed += 1
            last = "Failed"
        rows.append({
            "name": daily.name,
            "time": f"{daily.hour}:{daily.minute:02d}",
            "next_run": schedule.next_run if schedule else None,
            "last_result": last,
            "last_run": task.stopped if task else None,
        })
    label = "Scheduled tasks"
    if missing:
        return StatusItem("schedules", label, WARNING, f"{missing} of {len(SCHEDULES)} daily tasks are not set up.",
                          fix="Run once: python manage.py setup_schedules", rows=rows)
    if failed:
        return StatusItem("schedules", label, WARNING, f"The last run of {failed} task{'s' if failed != 1 else ''} failed.",
                          fix="Open the failed task in the administration site (Django Q → Failed tasks) to see why.", rows=rows)
    return StatusItem("schedules", label, OK, f"All {len(SCHEDULES)} daily tasks are set up.", rows=rows)


def email_status() -> StatusItem:
    """Console or real SMTP (settings.MAILERS["default"]) and the from address. Never the SMTP user or password."""
    mailer = settings.MAILERS["default"]
    sender = ("From address", settings.DEFAULT_FROM_EMAIL)
    if prints_emails():
        if settings.DEBUG:
            return StatusItem("email", "Email", INFO, "Development mode: emails are printed in the terminal.",
                              details=[("Mode", "Console (development)"), sender])
        return StatusItem("email", "Email", WARNING, "Emails are printed in the terminal, not sent.",
                          fix="Set EMAIL_REAL_SENDING=True and the EMAIL_HOST settings in .env, then restart the server and qcluster.",
                          details=[("Mode", "Console"), sender])
    options = mailer.get("OPTIONS", {})
    server = f"{options.get('host', '')}:{options.get('port', '')}"
    return StatusItem("email", "Email", OK, "Emails are sent through SMTP.",
                      details=[("Mode", "Real SMTP"), ("Server", server), sender])


def gemini_status() -> StatusItem:
    """Only whether a key is set: this page never calls Gemini (and never shows the key)."""
    details = [("Answer model", settings.GEMINI_CHAT_MODEL), ("Embedding model", settings.GEMINI_EMBEDDING_MODEL)]
    if settings.GEMINI_API_KEY:
        return StatusItem("gemini", "AI assistant (Gemini)", OK, "An API key is set (not tested on this page).",
                          fix="To test the key with real calls: python manage.py check_gemini", details=details)
    return StatusItem("gemini", "AI assistant (Gemini)", WARNING, "No GEMINI_API_KEY: the assistant and indexing cannot work.",
                      fix="Add GEMINI_API_KEY to .env, open a new terminal, then run: python manage.py check_gemini", details=details)


def pdf_status() -> StatusItem:
    """Render one line in memory (nothing is saved), like check_pdf."""
    from certificates.pdf import get_renderer

    label = "PDF rendering (WeasyPrint)"
    try:
        pdf = get_renderer().render(PDF_TEST_HTML)
    except Exception as error:  # a missing system library is the usual cause
        return StatusItem("pdf", label, WARNING, f"PDFs cannot be made: {type(error).__name__}.",
                          fix="Install Pango (CLAUDE.md, WeasyPrint setup), open a new terminal, then run: python manage.py check_pdf")
    if not pdf.startswith(b"%PDF-"):
        return StatusItem("pdf", label, WARNING, "The renderer did not return a PDF.", fix="Run: python manage.py check_pdf")
    return StatusItem("pdf", label, OK, "A test PDF was rendered in memory.")


def indexing_status() -> StatusItem:
    from assistant.indexing import pending_documents

    count = pending_documents().count()
    label = "Documents for the AI assistant"
    if count:
        return StatusItem("indexing", label, WARNING, f"{count} approved document{'s are' if count != 1 else ' is'} not indexed yet.",
                          fix="They are indexed at 7:30 each morning while qcluster runs, or now: python manage.py index_documents")
    return StatusItem("indexing", label, OK, "Every approved document that can be searched is indexed.")


def certificate_email_status() -> StatusItem:
    from certificates.services import pending_emails

    count = pending_emails().count()
    label = "Certificate emails"
    if count:
        return StatusItem("certificates", label, WARNING, f"{count} issued certificate{'s are' if count != 1 else ' is'} not sent yet.",
                          fix="They are retried at 8:30 each morning while qcluster runs, or now: python manage.py retry_certificates")
    return StatusItem("certificates", label, OK, "Every issued certificate has been emailed.")


def system_status() -> list[StatusItem]:
    """Every check except the PDF one (pdf_status, loaded separately)."""
    return [qcluster_status(), schedule_status(), email_status(), gemini_status(), indexing_status(), certificate_email_status()]


def checked_at() -> datetime:
    from django.utils import timezone

    return timezone.localtime()
