"""Approvals (ASSUMPTIONS 437), mounted at /approvals/."""

from django.urls import path

from . import page_views

app_name = "approvals"

urlpatterns = [
    path("", page_views.inbox_page, name="inbox"),  # "For your approval"
    path("submissions/", page_views.all_submissions, name="all"),  # can_approve_documents
    path("mine/", page_views.mine, name="mine"),
    path("submit/", page_views.submit_page, name="submit"),
    path("<int:submission_id>/", page_views.detail, name="detail"),
    path("<int:submission_id>/approve/", page_views.approve, name="approve"),
    path("<int:submission_id>/return/", page_views.return_submission, name="return"),
    path("<int:submission_id>/unapprove/", page_views.unapprove, name="unapprove"),
    path("<int:submission_id>/resubmit/", page_views.resubmit, name="resubmit"),
    path("<int:submission_id>/save/", page_views.save_to_documents, name="save"),
    path("<int:submission_id>/sign-preview/", page_views.sign_preview, name="sign_preview"),
    path("<int:submission_id>/file/<str:which>/", page_views.download, name="file"),
    # Assignments (ASSUMPTIONS 437)
    path("assigned/", page_views.assigned, name="assigned"),
    path("assign/", page_views.assign, name="assign"),
    path("assign/any/", page_views.assign_any, name="assign_any"),  # the general "Assign work" form (ASSUMPTIONS 468)
    path("assignments/<int:assignment_id>/done/", page_views.assignment_done, name="assignment_done"),
    path("assignments/<int:assignment_id>/remove/", page_views.assignment_remove, name="assignment_remove"),
]
