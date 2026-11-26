"""`manage.py seed_testing --reset` (ASSUMPTIONS 464): the user-testing database, built through the real services.

One run on an emptied test database at a fixed moment (never today's date) checks every state the user testing needs.
"""

from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
from django.core import mail
from django.core.management import CommandError, call_command
from django.db import connection

TESTING_DATE = date(2026, 11, 26)


@pytest.fixture
def testing_settings(settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path / "media_testing"
    return settings


def run(*args) -> str:
    out = StringIO()
    call_command("seed_testing", *args, stdout=out)
    return out.getvalue()


@pytest.mark.django_db
def test_refuses_without_debug(settings) -> None:
    settings.DEBUG = False
    with pytest.raises(CommandError, match="DEBUG is False"):
        run("--reset")


@pytest.mark.django_db
def test_refuses_without_reset(testing_settings) -> None:
    from accounts.models import TBIMember

    with pytest.raises(CommandError, match="--reset"):
        run()
    assert TBIMember.objects.count() == 0


@pytest.mark.django_db
def test_refuses_to_empty_the_project_folder(testing_settings) -> None:
    testing_settings.MEDIA_ROOT = testing_settings.BASE_DIR
    with pytest.raises(CommandError, match="refusing to empty it"):
        run("--reset")


@pytest.mark.django_db
def test_rejects_a_bad_checkin_time(testing_settings) -> None:
    with pytest.raises(CommandError, match="HH:MM"):
        run("--reset", "--checkin-start", "1pm")


@pytest.fixture
def seeded(testing_settings, clock, django_db_blocker, request):
    """Seed once at 9:00 on the testing date (transactional: --reset flushes the database)."""
    clock(datetime(2026, 11, 26, 9, 0))
    (testing_settings.MEDIA_ROOT / "old").mkdir(parents=True)
    (testing_settings.MEDIA_ROOT / "old" / "file.txt").write_text("old upload")
    output = run("--reset", "--testing-date", TESTING_DATE.isoformat(), "--checkin-start", "13:00")
    return {"output": output, "media": testing_settings.MEDIA_ROOT}


@pytest.mark.django_db(transaction=True)
def test_seed_testing_creates_every_state(seeded) -> None:  # noqa: PLR0915 - one run, many checks
    from django.utils import timezone

    from accounts.models import TBIMember
    from activities import services as activity_services
    from activities.models import Activity, ActivityAuditLog
    from approvals.models import Submission, WorkAssignment
    from assistant.models import DocumentEmbedding
    from certificates import services as certificate_services
    from certificates.models import Certificate
    from documents import services as document_services
    from documents.models import Document
    from notifications.models import Notification
    from registrations import attendance
    from registrations.models import (
        AttendanceDay, EvaluationAttachment, EvaluationSubmission, ParticipantSignature, Registration,
    )
    from reports.models import GeneratedReport, PaperSheetRead, ReportDraft

    output, media = seeded["output"], seeded["media"]
    assert "EMPTYING database" in output and str(media) in output
    assert not (media / "old").exists()  # the old uploads were removed

    # Accounts: the real names, positions, no forced password change, a printed password each, @example.com only.
    members = {m.name: m for m in TBIMember.objects.select_related("role", "position")}
    assert set(members) == {"Regine Paclibar", "Riah E. Encarnacion, DIT", "John Mark B. Correa", "Mary Pilar Barber",
                            "Jenelyn Asis", "Teodoro Jr. Abrea", "Meledy Ganoy", "Adelberto G. Laplana"}
    assert members["Regine Paclibar"].role.can_manage_users
    director = members["Riah E. Encarnacion, DIT"]
    assert director.role.role_name == "Director" and director.position.name == "Director, TBI & Manager, NAVIGATU"
    assert members["Jenelyn Asis"].position.name == "Manager, TARA"
    assert all(not m.must_change_password and m.email.endswith("@example.com") for m in members.values())
    assert all(m.has_usable_password() for m in members.values())
    assert output.count("Tbi-") == 8
    signatory = certificate_services.signatory_for(director)
    assert signatory is not None and signatory.signature_image_path

    # Activities in every state, both TBIs.
    by_title = {a.title: a for a in Activity.objects.select_related("status")}
    statuses = {a.status.name for a in by_title.values()}
    assert statuses == {"Draft", "Scheduled", "Ongoing", "Conducted"}
    assert any(a.description.startswith("NAVIGATU") for a in by_title.values())
    assert any(a.description.startswith("TARA") for a in by_title.values())
    assert {a.fund_source.code for a in by_title.values()} == {"RAF", "IGF", "TRF", "BRF"}
    assert all(a.budget_amount > 0 and a.objectives for a in by_title.values())

    draft = by_title["Startup Grant Proposal Writing Workshop"]
    assert draft.status.name == "Draft" and draft.creation_documents_required and not draft.documents.exists()

    scheduled = by_title["Aquaculture Startup Bootcamp: Tilapia and Seaweed Value Chains"]
    now = timezone.now()
    assert activity_services.open_for_registration(now=now).filter(pk=scheduled.pk).exists()
    assert certificate_services.is_prepared(scheduled) and not certificate_services.is_approved(scheduled)

    ongoing = by_title["Pitch Deck Clinic for Student Startups"]
    assert ongoing.status.name == "Ongoing"
    assert ongoing.start_date == TESTING_DATE - timedelta(days=1) and ongoing.end_date == TESTING_DATE
    assert ongoing.start_time == time(13, 0) and ongoing.end_time == time(16, 0)
    assert attendance.checkin_state(ongoing, timezone.make_aware(datetime(2026, 11, 26, 12, 15))).kind == "open"
    assert attendance.checkin_state(ongoing, timezone.make_aware(datetime(2026, 11, 26, 14, 1))).kind != "open"
    assert Registration.objects.filter(activity=ongoing, cancelled_at__isnull=False).count() == 3
    assert AttendanceDay.objects.filter(registration__activity=ongoing, day=ongoing.start_date).exists()
    assert not AttendanceDay.objects.filter(registration__activity=ongoing, day=TESTING_DATE).exists()
    assert Registration.objects.filter(activity=ongoing, registration_source="walk_in").exists()
    kinds = set(ParticipantSignature.objects.filter(registration__activity=ongoing).values_list("kind", flat=True))
    assert kinds == {"draw", "upload", "typed"}
    assert certificate_services.is_approved(ongoing)  # approved early: the live certificate flow works

    conducted = [a for a in by_title.values() if a.status.name == "Conducted"]
    assert len(conducted) == 4
    complete = by_title["Smart Farming Technologies Forum for Agri-Startups"]
    waiting = by_title["Intellectual Property and Branding Seminar for Startups"]
    closed = by_title["Startup Financial Literacy Workshop"]
    for activity in (complete, waiting):
        days = AttendanceDay.objects.filter(registration__activity=activity)
        assert days.filter(method="qr").exists() and days.filter(method="staff").exists()
        assert Registration.objects.filter(activity=activity, registration_source="walk_in").exists()
        assert EvaluationSubmission.objects.filter(registration__activity=activity).exists()
    assert len(set(AttendanceDay.objects.filter(registration__activity=complete).values_list("day", flat=True))) == 2
    photos = EvaluationAttachment.objects.filter(submission__registration__activity=complete)
    assert photos.filter(use_in_reports=True).exists() and photos.filter(use_in_reports=False).exists()
    generated = Document.objects.filter(activity=complete, title__contains="(generated)")
    assert {d.document_type.name for d in generated} == {"Attendance Sheet", "Confirmation of Attendance"}
    assert closed.evaluation_closed_by_hand

    # Certificates in every state.
    states = set(Certificate.objects.values_list("status", flat=True))
    assert {"generated", "approved", "issued"} <= states
    assert Certificate.objects.filter(status="issued", sent_at__isnull=False, recipient_type="registration").exists()
    assert Certificate.objects.filter(activity=complete, recipient_type="resource_person", status="issued").exists()
    assert Certificate.objects.filter(activity=waiting, status="generated").exists()
    assert not certificate_services.is_approved(waiting)

    # For payment: complete on one, missing on others (Generate / Upload).
    missing = {entry.activity.pk: entry for entry in document_services.payment_missing(now=now)}
    assert complete.pk not in missing
    assert waiting.pk in missing and closed.pk in missing

    # Documents: real text, a scan, one waiting for the Director.
    assert Document.objects.filter(file_extension="pdf", document_type__name="Request Letter").exists()
    assert Document.objects.filter(file_extension="jpg", document_type__name="Attendance Sheet").count() >= 2
    waiting_documents = Document.objects.filter(status__status_name="For Review", reviewed_at__isnull=True)
    assert waiting_documents.filter(document_type__needs_review=True).count() >= 2

    # The Director's work.
    assert Submission.objects.filter(status="submitted").count() == 2
    assert Submission.objects.filter(status="returned").count() == 2  # returned + unapproved (back to the member)
    assert Submission.objects.filter(events__action="unapproved").exists()
    assignments = WorkAssignment.objects.all()
    assert set(assignments.values_list("kind", flat=True)) == set(WorkAssignment.Kind.values)
    assert assignments.filter(status="done").exists() and assignments.filter(status="open").count() == 7
    assert assignments.filter(allow_signing=True, kind="submission_review").exists()
    assert assignments.filter(status="open", due_date__lt=TESTING_DATE).exists()  # one overdue

    # Reports, drafts, a confirmed paper sheet.
    assert GeneratedReport.objects.count() == 3 and GeneratedReport.objects.exclude(file_path="").count() == 3
    report_draft = ReportDraft.objects.get(kind="activity_report")
    assert report_draft.versions.count() == 2 and report_draft.messages.count() >= 2
    assert ReportDraft.objects.filter(kind="accomplishment_report", date_range_start=date(2026, 7, 1),
                                      date_range_end=date(2026, 9, 30)).exists()
    assert ReportDraft.objects.filter(kind="request_letter", activity=draft).exists()
    assert PaperSheetRead.objects.filter(state="confirmed", activity=complete).exists()

    # Notifications, History, AI index, schedules; nothing emailed for real.
    assert Notification.objects.filter(member=director).exists()
    assert ActivityAuditLog.objects.filter(changed_by__isnull=True).exists()  # automatic moves by the System
    assert DocumentEmbedding.objects.exists()
    assert "AI index:" in output and "nothing was sent" in output
    from django_q.models import Schedule

    assert Schedule.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_seed_testing_runs_twice(testing_settings, clock) -> None:
    """Repeatable: the rehearsal run and the run before the session give the same data (passwords change)."""
    from activities.models import Activity
    from accounts.models import TBIMember

    clock(datetime(2026, 11, 26, 9, 0))
    run("--reset", "--testing-date", TESTING_DATE.isoformat(), "--skip-ai-index")
    first = sorted(Activity.objects.values_list("title", flat=True))
    output = run("--reset", "--testing-date", TESTING_DATE.isoformat(), "--skip-ai-index")
    assert sorted(Activity.objects.values_list("title", flat=True)) == first
    assert TBIMember.objects.count() == 8
    assert "skipped (--skip-ai-index)" in output
