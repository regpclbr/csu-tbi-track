from django.apps import AppConfig


class RegistrationsConfig(AppConfig):
    name = "registrations"

    def ready(self) -> None:
        # Participant photos (ASSUMPTIONS 435): remove the stored file after the commit whenever a row is deleted, also
        # through a cascade (submission, registration, activity deletion).
        from django.db.models.signals import post_delete

        from . import evaluation_photos
        from .models import EvaluationAttachment

        post_delete.connect(evaluation_photos.remove_file_after_delete, sender=EvaluationAttachment,
                            dispatch_uid="evaluation_attachment_remove_file")
