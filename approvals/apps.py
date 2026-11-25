from django.apps import AppConfig


class ApprovalsConfig(AppConfig):
    """Work submitted to the Director (SUBMISSION, SUBMISSION_EVENT) and the "For your approval" inbox (ASSUMPTIONS 437)."""

    name = "approvals"
