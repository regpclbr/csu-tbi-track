"""Generate a report right away (without waiting for `qcluster`), through the same services as the API.

    python manage.py generate_report --type accomplishment --format pdf --start 2026-01-01 --end 2026-06-30
    python manage.py generate_report --type participation --format excel --start 2026-01-01 --end 2026-12-31
    python manage.py generate_report --type accomplishment --format pdf --start 2026-01-01 --end 2026-12-31 --sections summary,ai_summary,activities
    python manage.py generate_report --report 12        # build a report that is still pending (or failed)

Acts as --member <email> (default: the first active member who can create activities; never the System Administrator).
Prints the report ID, the file on disk and the download URL.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from accounts.models import Role, TBIMember
from reports import services
from reports.content import REPORT_TYPES
from reports.models import GeneratedReport


class Command(BaseCommand):
    help = "Generate a report now (PDF or Excel), through the same services as the API."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--type", choices=list(REPORT_TYPES), help="Report type.")
        parser.add_argument("--format", choices=["pdf", "excel"], default="pdf")
        parser.add_argument("--start", help="First day, YYYY-MM-DD (required, as in the API).")
        parser.add_argument("--end", help="Last day, YYYY-MM-DD, on or after --start (required).")
        parser.add_argument("--activity-type", help="Activity type ID (optional filter).")
        parser.add_argument("--sections", help="Comma-separated section keys (default: the type's sections).")
        parser.add_argument("--member", help="Email of the member generating it (default: the first active member who can create activities).")
        parser.add_argument("--report", type=int, help="Build an existing report that has no file yet.")

    def handle(self, *args, **options) -> None:
        if options["report"]:
            report = GeneratedReport.objects.filter(pk=options["report"]).first()
            if report is None:
                raise CommandError(f"Report {options['report']} does not exist.")
        else:
            if not options["type"]:
                raise CommandError("--type is required (or use --report <id>).")
            report = self.request(options)
        self.stdout.write(f"Report {report.pk}: building {report.format} ...")
        try:
            result = services.build_report(report.pk)
        except Exception as error:
            raise CommandError(f"Report {report.pk} failed: {type(error).__name__}: {error}") from error
        report.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"Report {report.pk}: {result}."))
        self.stdout.write(f"File on disk: {report.file_path.path}")
        self.stdout.write(f"Download (logged in): {settings.SITE_URL}{reverse('reports:download', args=[report.pk])}")
        if "ai_summary" in services.sections_of(report):
            if report.ai_prompt:
                self.stdout.write("AI prompt saved in ai_prompt (see /reports/api/<id>/ or the Django admin).")
            else:
                self.stdout.write(self.style.WARNING("AI summary: nothing was sent to Gemini (see the AI section of the report for the reason)."))

    def request(self, options) -> GeneratedReport:
        member = self.member(options["member"])
        data = {
            "report_type": options["type"],
            "format": options["format"],
            "date_range_start": options["start"],
            "date_range_end": options["end"],
            "activity_type": options["activity_type"],
            "included_sections": [s.strip() for s in options["sections"].split(",") if s.strip()] if options["sections"] else None,
        }
        try:
            return services.request_report(member, data, queue=False)
        except ValidationError as error:
            raise CommandError("; ".join(f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items())) from error
        except PermissionDenied as error:
            raise CommandError(str(error)) from error

    def member(self, email: str | None) -> TBIMember:
        if email:
            member = TBIMember.objects.filter(email__iexact=email.strip(), is_active=True).first()
            if member is None:
                raise CommandError(f"No active member with the email {email}.")
            return member
        # By the permission flag, never the role name: the System Administrator cannot generate reports (ASSUMPTIONS 339).
        member = TBIMember.objects.filter(role__can_create_activity=True, is_active=True).order_by("pk").first()
        if member is None:
            raise CommandError("No active member who can create activities found. Use --member <email>.")
        return member
