"""The general "Assign work" dialog and Assign where the Director's work happens (ASSUMPTIONS 468): every kind in plain
words, only the work that can be assigned now, a target per kind, Assign on each For-your-approval item, the Dashboard
and the Approvals pages; the work's own dialogs offer only what can still be assigned. Every rule stays in
approvals.assignments.assign_work."""

import pytest
from django.urls import reverse
from django.utils import timezone

from approvals import assignments, pages
from approvals.models import WorkAssignment
from documents import services as document_services
from documents.models import DocumentType

from .conftest import NOW, pdf_file

pytestmark = pytest.mark.django_db
Kind = WorkAssignment.Kind
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture
def waiting_document(roles, staff, activity):
    DocumentType.objects.filter(name="Invitation Letter").update(needs_review=True)
    letter = DocumentType.objects.get(name="Invitation Letter")
    return document_services.upload_document(staff, activity, letter, pdf_file(), "Invitation", now=NOW)


@pytest.fixture
def prepared(activity, director):
    """The activity's certificates prepared before Conducted (ASSUMPTIONS 420), so their approval can be assigned."""
    type(activity).objects.filter(pk=activity.pk).update(certificates_prepared_at=timezone.now(),
                                                         certificates_prepared_by=director)
    activity.refresh_from_db()
    return activity


@pytest.fixture
def everything(waiting_document, prepared, submit, staff, director_signature):
    return {"document": waiting_document, "activity": prepared, "submission": submit(staff, "Terminal report")}


def test_every_kind_has_plain_words_and_open_targets(director, everything) -> None:
    choices = {c["kind"]: c for c in assignments.general_choices(director)}
    assert set(choices) == set(Kind.values)  # the Director may assign every kind
    assert all(c["label"] and "_" not in c["label"] for c in choices.values())
    assert choices[Kind.DOCUMENT_REVIEW]["targets"][0][0] == everything["document"].pk
    assert choices[Kind.SUBMISSION_REVIEW]["targets"][0][0] == everything["submission"].pk
    assert choices[Kind.CERTIFICATE_APPROVAL]["targets"][0][0] == everything["activity"].pk
    for kind in (Kind.ACTIVITY, Kind.SIGNATORY_CHOICE):
        assert [pk for pk, _ in choices[kind]["targets"]] == [everything["activity"].pk]
    for kind in (Kind.REPORT, Kind.CERTIFICATE_TEXT, Kind.SIGNATORIES):
        assert choices[kind]["field"] is None and choices[kind]["reason"] == ""


def test_assigned_work_leaves_the_list_with_the_reason(director, head, everything) -> None:
    assignments.assign_work(director, Kind.DOCUMENT_REVIEW, head, document=everything["document"])
    assignments.assign_work(director, Kind.CERTIFICATE_TEXT, head)
    choices = {c["kind"]: c for c in assignments.general_choices(director)}
    assert choices[Kind.DOCUMENT_REVIEW]["targets"] == []
    assert choices[Kind.DOCUMENT_REVIEW]["reason"] == "No document is waiting for review."
    assert "already assigned" in choices[Kind.CERTIFICATE_TEXT]["reason"]


def test_staff_never_get_the_general_dialog(staff, head, client) -> None:
    assert pages.general_assign_panel(staff) is None
    client.force_login(staff)
    assert client.get(reverse("approvals:assign_any")).status_code == 403
    assert "data-assign-any-trigger" not in client.get(reverse("home")).content.decode()


@pytest.mark.parametrize("kind", Kind.values)
def test_the_general_dialog_assigns_every_kind(kind, director, head, everything, client) -> None:
    client.force_login(director)
    form = client.get(reverse("approvals:assign_any") + f"?kind={kind}", **HTMX).content.decode()
    assert f'value="{kind}" selected' in form
    data = {"kind": kind, "assigned_to": head.pk, "next": reverse("approvals:inbox"), "note": "Please",
            "report_title": "Accomplishment Report - Q4 2026"}
    field = assignments.target_field(kind)
    if field is not None:
        target = {"activity": everything["activity"], "document": everything["document"],
                  "submission": everything["submission"]}[field]
        data[f"target-{kind}"] = target.pk
        assert f'name="target-{kind}"' in form
    response = client.post(reverse("approvals:assign"), data)
    assert response.status_code == 302
    assignment = WorkAssignment.objects.get(kind=kind)
    assert assignment.assigned_to == head and assignment.assigned_by == director
    if field is not None:
        assert getattr(assignment, field) == target


def test_may_stamp_my_signature_from_the_general_dialog(director, head, everything, client) -> None:
    client.force_login(director)
    form = client.get(reverse("approvals:assign_any") + "?kind=submission_review", **HTMX).content.decode()
    assert "May stamp my signature" in form
    client.post(reverse("approvals:assign"), {"kind": "submission_review", "assigned_to": head.pk, "allow_signing": "yes",
                                              "target-submission_review": everything["submission"].pk})
    assert WorkAssignment.objects.get(kind="submission_review").allow_signing


def test_the_dashboard_and_the_approvals_pages_show_assign_work(director, everything, client) -> None:
    client.force_login(director)
    for url in (reverse("home"), reverse("approvals:inbox"), reverse("approvals:mine")):
        assert "data-assign-any-trigger" in client.get(url).content.decode(), url
    page = client.get(reverse("approvals:assign_any")).content.decode()  # without JavaScript: its own page
    assert 'id="assign-any-kind"' in page and "<!doctype html>" in page.lower()


def test_each_inbox_item_has_assign_with_its_work_chosen(director, head, everything, client) -> None:
    client.force_login(director)
    html = client.get(reverse("approvals:inbox")).content.decode()
    for kind, target in (("document_review", everything["document"].pk), ("submission_review", everything["submission"].pk),
                         ("certificate_approval", everything["activity"].pk)):
        assert f"kind: '{kind}', target: '{target}'" in html
    form = client.get(reverse("approvals:assign_any") + f"?kind=document_review&target={everything['document'].pk}",
                      **HTMX).content.decode()
    assert f'value="{everything["document"].pk}" selected' in form
    # Assigned work keeps its Open button but loses Assign.
    assignments.assign_work(director, Kind.DOCUMENT_REVIEW, head, document=everything["document"])
    html = client.get(reverse("approvals:inbox")).content.decode()
    assert "kind: 'document_review'" not in html


def test_the_works_own_dialog_offers_only_what_can_still_be_assigned(director, head, everything) -> None:
    activity = everything["activity"]
    kinds = [kind for kind, _ in pages.assign_panel(director, activity=activity)["kinds"]]
    assert kinds == [Kind.ACTIVITY, Kind.CERTIFICATE_APPROVAL, Kind.SIGNATORY_CHOICE]
    assignments.assign_work(director, Kind.SIGNATORY_CHOICE, head, activity=activity)
    kinds = [kind for kind, _ in pages.assign_panel(director, activity=activity)["kinds"]]
    assert Kind.SIGNATORY_CHOICE not in kinds
