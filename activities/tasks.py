"""Background task for activities, run by the Django-Q2 cluster (`python manage.py qcluster`) every
ACTIVITY_STATUS_INTERVAL_MINUTES (created by `setup_schedules`)."""

import logging

from registrations import services as registration_services

from . import services

logger = logging.getLogger("activities")


def run_activity_automation() -> dict[str, int]:
    """1. The automatic status moves (Scheduled -> Ongoing -> Conducted, ASSUMPTIONS 346).
    2. The first evaluation link to each attendee of a Conducted activity (ASSUMPTIONS 348): sent here, inside
       qcluster, one locked registration at a time, so nobody gets two emails.
    Page loads only do step 1; emails are sent only by this task."""
    moves = services.apply_automatic_statuses()
    links = registration_services.send_automatic_evaluation_links()
    result = {"status_changes": moves, **links}
    if moves or links["sent"] or links["failed"]:
        logger.info("Activity automation: %s", result)
    return result
