from django.apps import AppConfig


class ReportsConfig(AppConfig):
    name = "reports"

    def ready(self) -> None:
        # Report draft files (ASSUMPTIONS 442): remove the stored file after the commit whenever a row is deleted, also
        # through a cascade (the draft, or the activity deleted with item 389). A rollback keeps the file.
        from django.db.models.signals import post_delete

        from . import generator
        from .models import ReportAttachment

        post_delete.connect(generator.remove_file_after_delete, sender=ReportAttachment,
                            dispatch_uid="report_attachment_remove_file")
