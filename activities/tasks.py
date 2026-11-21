"""Background task for activities, run by the Django-Q2 cluster (`python manage.py qcluster`) every
ACTIVITY_STATUS_INTERVAL_MINUTES (created by `setup_schedules`)."""

import logging

from registrations import services as registration_services

from . import services

logger = logging.getLogger("activities")


def run_activity_automation() -> dict[str, int]:
    """1. The automatic status moves (Scheduled -> Ongoing -> Conducted, ASSUMPTIONS 346).
    2. The missing certificates of Conducted activities (ASSUMPTIONS 362: a catch-up; the move to Conducted already
       creates them, unless the certificate design was incomplete then).
    3. Certificate PDFs that should have been rendered more than 10 minutes ago and were not (ASSUMPTIONS 368: a
       task never queued or lost) are queued again; the ready certificates of approved activities are approved
       (ASSUMPTIONS 369: a catch-up, an evaluation already does it).
    4. The first evaluation link to each attendee of a Conducted activity (ASSUMPTIONS 348), or of an Ongoing one from
       EVALUATION_LINK_HOURS_BEFORE_END hours before its end once the evaluation is open (ASSUMPTIONS 419): sent here,
       inside qcluster, one locked registration at a time, so nobody gets two emails.
    Page loads only do step 1 (with its certificates); emails are sent only by this task."""
    from certificates.services import (
        approve_pending_certificates,
        generate_pending_certificates,
        queue_stalled_preparations,
    )

    moves = services.apply_automatic_statuses()
    certificates = generate_pending_certificates()
    approved = approve_pending_certificates()
    stalled = queue_stalled_preparations()
    links = registration_services.send_automatic_evaluation_links()
    result = {"status_changes": moves, "certificates_created": certificates, "certificates_approved": approved,
              "pdfs_queued": stalled, **links}
    if moves or certificates or approved or stalled or links["sent"] or links["failed"]:
        logger.info("Activity automation: %s", result)
    return result
