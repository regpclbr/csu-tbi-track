"""Assign work (WORK_ASSIGNMENT, ASSUMPTIONS 437): who may assign, permission for THAT object only and only while open,
"on behalf of" logging, automatic / manual completion, removal, the separation rules, the role flag, the pages."""

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import Permission, has_permission_for
from accounts import services as account_services
from activities import services as activity_services
from activities.models import ActivityAuditLog
from approvals import assignments, inbox, services
from approvals.models import Submission, SubmissionEvent, WorkAssignment
from documents import services as document_services
from documents.models import DocumentType
from notifications.models import Notification

from .conftest import NOW, pdf_file

pytestmark = pytest.mark.django_db
K = WorkAssignment.Kind


def client_for(member) -> Client:
    client = Client()
    client.force_login(member)
    return client


@pytest.fixture
def other_activity(activity, director):
    from activities.models import Activity

    clone = Activity.objects.get(pk=activity.pk)
    clone.pk = None
    clone.title = "Another workshop"
    clone.save()
    clone.participant_categories.set(activity.participant_categories.all())
    return clone


@pytest.fixture
def waiting_document(roles, staff, activity):
    letter = DocumentType.objects.get(name="Invitation Letter")
    DocumentType.objects.filter(pk=letter.pk).update(needs_review=True)
    letter.refresh_from_db()
    return document_services.upload_document(staff, activity, letter, pdf_file(), "Invitation", now=NOW)


# --- The role flag ---------------------------------------------------------------------------------------------------


def test_only_the_director_is_seeded_with_can_assign_work(roles) -> None:
    assert {r.role_name for r in Role.objects.filter(can_assign_work=True)} == {"Director"}


def test_can_assign_work_is_never_with_manage_users(roles, system_admin) -> None:
    with pytest.raises(account_services.MemberManagementError, match="assign work"):
        account_services.set_role_work_assignment(system_admin, Role.objects.get(role_name="System Administrator"), True)
    head_role = Role.objects.get(role_name="Incubation Project Head")
    account_services.set_role_work_assignment(system_admin, head_role, True)
    assert Role.objects.get(pk=head_role.pk).can_assign_work


def test_the_roles_screen_switch(roles, system_admin) -> None:
    role = Role.objects.get(role_name="Admin Staff")
    client_for(system_admin).post(reverse("accounts:console_role_work_assignment", args=[role.pk]), {"enabled": "true"})
    assert Role.objects.get(pk=role.pk).can_assign_work


# --- Who may assign ------------------------------------------------------------------------------------------------


def test_assigning_needs_the_flag_and_the_flag_of_the_work(activity, staff, head, director) -> None:
    with pytest.raises(PermissionDenied):
        assignments.assign_work(staff, K.ACTIVITY, head, activity=activity)  # no can_assign_work
    Role.objects.filter(role_name="Admin Staff").update(can_assign_work=True)
    staff.role.refresh_from_db()
    with pytest.raises(PermissionDenied):  # Admin Staff cannot approve certificates, so cannot hand it out
        assignments.assign_work(staff, K.CERTIFICATE_APPROVAL, head, activity=activity)


def test_never_to_yourself_and_only_one_open_per_work(activity, director, head, staff) -> None:
    with pytest.raises(ValidationError):
        assignments.assign_work(director, K.ACTIVITY, director, activity=activity)
    assignments.assign_work(director, K.ACTIVITY, head, activity=activity)
    with pytest.raises(assignments.AssignmentError, match="already assigned"):
        assignments.assign_work(director, K.ACTIVITY, staff, activity=activity)


def test_due_date_in_the_past_is_refused(activity, director, head) -> None:
    with pytest.raises(ValidationError):
        assignments.assign_work(director, K.ACTIVITY, head, activity=activity, due_date=timezone.localdate().replace(year=2020))


# --- Permission for that object only -----------------------------------------------------------------------------


def test_an_assignment_gives_permission_for_that_object_only(activity, other_activity, director, view_only) -> None:
    assert not has_permission_for(view_only, Permission.CREATE_ACTIVITY, activity)
    assignment = assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity, note="Fix the venue")
    assert has_permission_for(view_only, Permission.CREATE_ACTIVITY, activity)
    assert not has_permission_for(view_only, Permission.CREATE_ACTIVITY, other_activity)
    assert not has_permission_for(view_only, Permission.APPROVE_CERTIFICATES, activity)  # another kind of work
    activity_services.update_activity(view_only, activity, {"venue": "Room 2"})
    with pytest.raises(PermissionDenied):
        activity_services.update_activity(view_only, other_activity, {"venue": "Room 3"})
    note = Notification.objects.get(type=Notification.WORK_ASSIGNED)
    assert note.member == view_only and note.work_assignment == assignment and "Fix the venue" in note.message


def test_the_permission_ends_when_removed_or_done(activity, director, view_only) -> None:
    assignment = assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity)
    assignments.remove_assignment(director, assignment)
    assert not has_permission_for(view_only, Permission.CREATE_ACTIVITY, activity)
    with pytest.raises(PermissionDenied):
        activity_services.update_activity(view_only, activity, {"venue": "Room 2"})
    assert Notification.objects.filter(member=view_only, type=Notification.WORK_ASSIGNMENT_REMOVED).exists()
    again = assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity)
    assignments.mark_done(view_only, again)
    assert not has_permission_for(view_only, Permission.CREATE_ACTIVITY, activity)
    assert Notification.objects.filter(member=director, type=Notification.WORK_ASSIGNMENT_DONE).exists()


def test_an_inactive_assignee_loses_it(activity, director, view_only) -> None:
    assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity)
    view_only.is_active = False
    view_only.save()
    assert not has_permission_for(view_only, Permission.CREATE_ACTIVITY, activity)


def test_the_role_never_changes(activity, director, view_only) -> None:
    before = (view_only.role_id, Role.objects.get(pk=view_only.role_id).can_create_activity)
    assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity)
    view_only.refresh_from_db()
    assert (view_only.role_id, Role.objects.get(pk=view_only.role_id).can_create_activity) == before


# --- "On behalf of" ---------------------------------------------------------------------------------------------


def test_edits_through_an_assignment_are_logged_on_behalf_of_the_assigner(activity, director, view_only, staff) -> None:
    assignments.assign_work(director, K.ACTIVITY, view_only, activity=activity)
    activity_services.update_activity(view_only, activity, {"venue": "Room 2"})
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Venue").latest("pk")
    assert row.changed_by == view_only and row.on_behalf_of == director
    from activities.formatting import changed_by_name

    assert changed_by_name(row) == f"{view_only.name} (on behalf of {director.name})"
    activity_services.update_activity(staff, activity, {"venue": "Room 3"})  # own flag: not on behalf of anyone
    assert ActivityAuditLog.objects.filter(activity=activity, field_changed="Venue").latest("pk").on_behalf_of is None


def test_the_assignment_itself_is_in_history(activity, director, head) -> None:
    assignments.assign_work(director, K.ACTIVITY, head, activity=activity)
    row = ActivityAuditLog.objects.filter(activity=activity, field_changed="Assignment").latest("pk")
    assert row.changed_by == director and row.new_value == f"Work on the activity assigned to {head.name}"


# --- Reviews through an assignment --------------------------------------------------------------------------------


def test_a_submission_review_through_an_assignment(submit, staff, director, head) -> None:
    submission = submit(staff)
    assignment = assignments.assign_work(director, K.SUBMISSION_REVIEW, head, submission=submission)  # no due date: never stale
    assert services.visible_submissions(head).filter(pk=submission.pk).exists()
    approved = services.approve_submission(head, submission)
    event = approved.events.get(action=SubmissionEvent.Action.APPROVED)
    assert event.member == head and event.on_behalf_of == director
    assert WorkAssignment.objects.get(pk=assignment.pk).status == WorkAssignment.Status.DONE  # done with the work
    assert not services.can_review_submission(head, Submission.objects.get(pk=submission.pk))


def test_an_assignee_never_signs(submit, staff, director, head, director_signature) -> None:
    submission = submit(staff)
    assignments.assign_work(director, K.SUBMISSION_REVIEW, head, submission=submission)
    assert services.sign_refused_reason(head, submission) == services.SIGN_NOT_DELEGATED
    with pytest.raises(services.SubmissionError, match="never delegated"):
        services.approve_submission(head, submission, sign=True)


def test_a_document_review_through_an_assignment(waiting_document, director, head) -> None:
    assignment = assignments.assign_work(director, K.DOCUMENT_REVIEW, head, document=waiting_document)
    assert document_services.allowed_actions(head, waiting_document)["approve"]
    document_services.return_document(head, waiting_document, "Wrong date")
    assert WorkAssignment.objects.get(pk=assignment.pk).status == WorkAssignment.Status.DONE


def test_a_certificate_approval_through_an_assignment(activity, director, view_only) -> None:
    from certificates import services as certificate_services

    assignment = assignments.assign_work(director, K.CERTIFICATE_APPROVAL, view_only, activity=activity)
    assert has_permission_for(view_only, Permission.APPROVE_CERTIFICATES, activity)
    assert not certificate_services.can_choose_signatories(view_only, activity)  # another kind of work
    assert assignment.status == WorkAssignment.Status.OPEN


def test_choosing_signatories_through_an_assignment(activity, director, view_only) -> None:
    from certificates import services as certificate_services

    assert not certificate_services.can_choose_signatories(view_only, activity)
    assignments.assign_work(director, K.SIGNATORY_CHOICE, view_only, activity=activity)
    assert certificate_services.can_choose_signatories(view_only, activity)
    certificate_services.require_signatory_choice(view_only, activity)
    assert not has_permission_for(view_only, Permission.APPROVE_CERTIFICATES, activity)  # never the approval


def test_a_report_to_prepare(director, view_only) -> None:
    from reports import services as report_services

    assert not report_services.can_generate(view_only)
    assignment = assignments.assign_work(director, K.REPORT, view_only, report_title="Accomplishment Report Q4")
    assert report_services.can_generate(view_only)
    assignments.mark_done(view_only, assignment)
    assert not report_services.can_generate(view_only)


# --- Separation of duties -----------------------------------------------------------------------------------------


def test_nobody_is_assigned_to_review_their_own_work(submit, staff, director, waiting_document, activity) -> None:
    with pytest.raises(assignments.AssignmentError, match="own work"):
        assignments.assign_work(director, K.SUBMISSION_REVIEW, staff, submission=submit(staff))
    with pytest.raises(assignments.AssignmentError, match="own work"):
        assignments.assign_work(director, K.DOCUMENT_REVIEW, staff, document=waiting_document)
    activity.created_by = staff
    activity.save(update_fields=["created_by"])
    with pytest.raises(assignments.AssignmentError, match="own work"):
        assignments.assign_work(director, K.CERTIFICATE_APPROVAL, staff, activity=activity)


def test_user_managers_never_get_work_their_role_may_not_have(activity, director, system_admin) -> None:
    with pytest.raises(ValidationError, match="manages users"):
        assignments.assign_work(director, K.ACTIVITY, system_admin, activity=activity)
    assignments.assign_work(director, K.CERTIFICATE_APPROVAL, system_admin, activity=activity)  # allowed by the flags


def test_closed_work_cannot_be_assigned(submit, staff, director, head) -> None:
    submission = services.approve_submission(director, submit(staff))
    with pytest.raises(assignments.AssignmentError, match="not waiting"):
        assignments.assign_work(director, K.SUBMISSION_REVIEW, head, submission=submission)


def test_only_the_assigner_or_another_assigner_removes_and_only_the_two_mark_done(activity, director, head, staff) -> None:
    assignment = assignments.assign_work(director, K.ACTIVITY, head, activity=activity)
    with pytest.raises(PermissionDenied):
        assignments.remove_assignment(staff, assignment)
    with pytest.raises(PermissionDenied):
        assignments.mark_done(staff, assignment)
    assignments.mark_done(head, assignment)
    with pytest.raises(assignments.AssignmentError, match="already done"):
        assignments.remove_assignment(director, assignment)


# --- Inbox, pages, dashboard ---------------------------------------------------------------------------------------


def test_overdue_assignments_are_in_the_assigners_inbox(activity, director, head, clock) -> None:
    from datetime import datetime, timedelta

    assignments.assign_work(director, K.ACTIVITY, head, activity=activity, due_date=timezone.localdate() + timedelta(days=1))
    assert [i.kind for i in inbox.inbox(director)] == []
    clock(datetime.combine(timezone.localdate() + timedelta(days=3), datetime.min.time()).replace(hour=9))
    items = inbox.inbox(director)
    assert [i.kind for i in items] == [inbox.ASSIGNMENT] and items[0].overdue
    assert inbox.inbox_counts(director)[inbox.ASSIGNMENT] == 1


def test_the_assign_dialog_and_the_pages(activity, director, view_only) -> None:
    page = client_for(director).get(reverse("activities:detail", args=[activity.pk]))
    assert b"data-assign-dialog" in page.content and b"Approve the certificates" in page.content
    assert b"data-assign-dialog" not in client_for(view_only).get(reverse("activities:detail", args=[activity.pk])).content
    response = client_for(director).post(reverse("approvals:assign"), {
        "kind": K.ACTIVITY, "assigned_to": view_only.pk, "activity": activity.pk, "note": "Please update the venue",
        "next": reverse("activities:detail", args=[activity.pk])})
    assert response.status_code == 302 and WorkAssignment.objects.filter(assigned_to=view_only).exists()
    as_assignee = client_for(view_only)
    detail = as_assignee.get(reverse("activities:detail", args=[activity.pk]))
    assert b"data-assigned-notice" in detail.content and reverse("activities:edit", args=[activity.pk]).encode() in detail.content
    assert as_assignee.get(reverse("activities:edit", args=[activity.pk])).status_code == 200
    mine = as_assignee.get(reverse("approvals:assigned"))
    assert b"Please update the venue" in mine.content
    home = as_assignee.get(reverse("home"))
    assert b"data-assigned-card" in home.content


def test_page_actions_follow_the_assignment(waiting_document, director, head) -> None:
    url = reverse("documents:approve", args=[waiting_document.pk])
    assert client_for(head).post(url).status_code == 403
    assignments.assign_work(director, K.DOCUMENT_REVIEW, head, document=waiting_document)
    client_for(head).post(url)
    waiting_document.refresh_from_db()
    assert waiting_document.review_state == waiting_document.APPROVED and waiting_document.reviewed_by == head
