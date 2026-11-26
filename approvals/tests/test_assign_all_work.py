"""Assign all the Director's work (ASSUMPTIONS 460): every Director-only action is listed with its kind (or "never");
two new kinds without an object (the default certificate text, the signatory list); "May stamp my signature" on a
submission review ("signed on behalf of"); the per-activity actions accept the activity's assignment. The rules of item
437 stay: one task until done or removed, "on behalf of" logging, never your own submission, never to a user manager."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from accounts.permissions import Permission
from activities.models import ActivityAuditLog
from approvals import assignments, services
from approvals.models import Submission, SubmissionEvent, WorkAssignment
from certificates import services as certificate_services
from certificates.models import CertificateSignatory
from notifications.models import Notification

from .conftest import NOW, pdf_file

pytestmark = pytest.mark.django_db
Kind = WorkAssignment.Kind


def test_every_director_only_action_is_listed_with_its_kind() -> None:
    listed = dict(assignments.ASSIGNABLE_ACTIONS)
    assert set(assignments.KIND_PERMISSIONS) == {kind for kind in listed.values() if kind} | {Kind.SIGNATORY_CHOICE}
    never = [action for action, kind in assignments.ASSIGNABLE_ACTIONS if kind is None]
    assert never == ["Unapprove a document", "Unapprove a submission", "Unapprove an activity's certificates",
                     "Link a signature to an account / change a signature linked to an account", "Assign work"]
    assert Kind.SIGNATORIES in assignments.NOT_FOR_USER_MANAGERS  # never with can_manage_users (check_role_flags)


# --- The default certificate text ----------------------------------------------------------------------------------------


def test_the_default_certificate_text_can_be_assigned(director, staff, client) -> None:
    values = {"title": "Certificate of Participation", "line_before_name": "is given to",
              "main_sentence": "for taking part in {activity} on {date}.", "closing_line": "Given on {given_date}."}
    with pytest.raises(PermissionDenied):
        certificate_services.save_default_text(staff, "participation", values)
    assignment = assignments.assign_work(director, Kind.CERTIFICATE_TEXT, staff)
    assert assignment.activity is None and assignments.target_url(assignment) == reverse("certificates:text")
    certificate_services.save_default_text(staff, "participation", values)
    client.force_login(staff)
    assert client.get(reverse("certificates:text")).status_code == 200
    with pytest.raises(assignments.AssignmentError, match="already assigned"):
        assignments.assign_work(director, Kind.CERTIFICATE_TEXT, staff)
    assignments.mark_done(staff, assignment)
    with pytest.raises(PermissionDenied):
        certificate_services.save_default_text(staff, "participation", values)
    assert client.get(reverse("certificates:text")).status_code == 403


# --- The signatory list --------------------------------------------------------------------------------------------------


def test_the_signatory_list_can_be_assigned_but_never_a_linked_signature(director, staff, director_signature,
                                                                          client) -> None:
    with pytest.raises(PermissionDenied):
        certificate_services.add_signatory(staff, "Dr. Ana Santos", "Vice President")
    assignments.assign_work(director, Kind.SIGNATORIES, staff)
    person = certificate_services.add_signatory(staff, "Dr. Ana Santos", "Vice President")
    certificate_services.edit_signatory(staff, person, "Dr. Ana M. Santos", "Vice President for RDIE")
    with pytest.raises(PermissionDenied, match="linked to an account"):  # the Director's own stamp
        certificate_services.edit_signatory(staff, director_signature, "Someone Else", "Director")
    with pytest.raises(PermissionDenied):  # linking accounts stays with the flag
        certificate_services.link_signatory_account(staff, person, staff)
    client.force_login(staff)
    assert client.get(reverse("certificates:signatories")).status_code == 200


def test_a_user_manager_is_never_given_the_signatory_list(director, system_admin) -> None:
    with pytest.raises(ValidationError, match="manages users"):
        assignments.assign_work(director, Kind.SIGNATORIES, system_admin)


def test_the_assign_dialog_on_the_two_pages(director, client) -> None:
    client.force_login(director)
    for name in ("certificates:signatories", "certificates:text"):
        page = client.get(reverse(name)).content.decode()
        assert "data-assign-trigger" in page, name


# --- Reissue through the certificate approval assignment -------------------------------------------------------------------


def test_reissue_accepts_the_activitys_certificate_approval_assignment(director, staff, activity, make_member) -> None:
    other = make_member("Admin Staff", email="other@example.com")
    with pytest.raises(PermissionDenied):
        certificate_services._require_reissue(staff, None, activity)
    assignments.assign_work(director, Kind.CERTIFICATE_APPROVAL, other, activity=activity)
    certificate_services._require_reissue(other, None, activity)  # allowed for THIS activity
    with pytest.raises(PermissionDenied):
        certificate_services._require_reissue(staff, None, activity)


# --- Signing on behalf of the Director ---------------------------------------------------------------------------------------


def test_may_stamp_my_signature_signs_with_the_directors_signature(director, staff, head, director_signature, activity,
                                                                    submit) -> None:
    submission = submit(staff, "Request letter", activity=activity)
    assignment = assignments.assign_work(director, Kind.SUBMISSION_REVIEW, head, submission=submission,
                                         allow_signing=True)
    assert assignment.allow_signing
    assert services.sign_refused_reason(head, submission) is None
    services.approve_submission(head, submission, sign=True, now=NOW)
    submission.refresh_from_db()
    assert submission.status == Submission.Status.APPROVED and submission.signed_by == head
    assert submission.signatory == director_signature  # the Director's signature, never the assignee's own
    signed = SubmissionEvent.objects.get(submission=submission, action=SubmissionEvent.Action.SIGNED)
    assert signed.member == head and signed.on_behalf_of == director
    history = ActivityAuditLog.objects.get(activity=activity, field_changed=services.SIGNED_ON_BEHALF_LABEL)
    assert "signed by" in history.new_value and f"on behalf of {director.name}" in history.new_value
    assert history.on_behalf_of == director
    assert Notification.objects.filter(member=director, message__contains="stamped your signature").exists()


def test_without_the_tick_an_assignee_approves_but_never_signs(director, staff, head, director_signature, submit) -> None:
    submission = submit(staff)
    assignments.assign_work(director, Kind.SUBMISSION_REVIEW, head, submission=submission)
    assert services.sign_refused_reason(head, submission) == services.SIGN_NOT_DELEGATED
    with pytest.raises(services.SubmissionError, match="did not allow"):
        services.approve_submission(head, submission, sign=True, now=NOW)


def test_signing_rules_of_the_assignment(director, staff, head, activity, submit, director_signature) -> None:
    with pytest.raises(ValidationError, match="Only a submission review"):
        assignments.assign_work(director, Kind.ACTIVITY, head, activity=activity, allow_signing=True)
    own = submit(head)  # never your own submission, also with the tick
    with pytest.raises(assignments.AssignmentError, match="own work"):
        assignments.assign_work(director, Kind.SUBMISSION_REVIEW, head, submission=own, allow_signing=True)


def test_the_tick_needs_the_directors_own_signature(director, staff, head, submit) -> None:
    submission = submit(staff)
    with pytest.raises(ValidationError, match="not linked to a signature"):
        assignments.assign_work(director, Kind.SUBMISSION_REVIEW, head, submission=submission, allow_signing=True)


def test_the_dialog_offers_the_tick_only_with_a_signature(director, staff, submit, director_signature) -> None:
    from approvals import pages

    submission = submit(staff)
    assert pages.assign_panel(director, submission=submission)["offers_signing"] is True
    director_signature.member = None
    director_signature.save(update_fields=["member"])
    assert pages.assign_panel(director, submission=submission)["offers_signing"] is False


# --- Per-activity actions accept the activity's assignment ----------------------------------------------------------------


def test_activity_actions_accept_the_activity_assignment(director, view_only, activity) -> None:
    from registrations import attendance_forms
    from registrations import services as registration_services

    with pytest.raises(PermissionDenied):
        registration_services.send_evaluation_links(view_only, activity)
    assignments.assign_work(director, Kind.ACTIVITY, view_only, activity=activity)
    with pytest.raises(registration_services.RegistrationError):  # allowed now: refused only by the evaluation rules
        registration_services.send_evaluation_links(view_only, activity)
    with pytest.raises(attendance_forms.FormError):  # allowed: refused by the form's own rule (nobody attended)
        attendance_forms.generate_form(view_only, activity, attendance_forms.ATTENDANCE)
