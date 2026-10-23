"""Print the dashboard numbers as JSON: the same functions and output as /reports/api/dashboard/.

    python manage.py show_dashboard                                   # this year (Manila)
    python manage.py show_dashboard --start 2026-01-01 --end 2026-06-30 --activity-type 2
"""

import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from config.json import LocalTimeJSONEncoder
from reports import metrics
from reports.services import filters_json


class Command(BaseCommand):
    help = "Print the dashboard numbers (cards and chart data) as JSON."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--start", help="YYYY-MM-DD (default: January 1 of this year).")
        parser.add_argument("--end", help="YYYY-MM-DD (default: December 31 of this year).")
        parser.add_argument("--activity-type", help="Activity type ID (optional).")

    def handle(self, *args, **options) -> None:
        try:
            filters = metrics.parse_filters(
                {"start": options["start"], "end": options["end"], "activity_type": options["activity_type"]}
            )
        except ValidationError as error:
            raise CommandError("; ".join(f"{field}: {' '.join(messages)}" for field, messages in error.message_dict.items())) from error
        data = {"filters": filters_json(filters), **metrics.dashboard(filters)}
        self.stdout.write(json.dumps(data, cls=LocalTimeJSONEncoder, indent=2, ensure_ascii=False))
