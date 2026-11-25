"""Index files for the assistant, by hand (ASSUMPTIONS 438). Safe to run again."""

from django.core.management.base import BaseCommand

from assistant import indexing


class Command(BaseCommand):
    help = (
        "Index every file the assistant may read that has no embeddings yet: activity documents (any review state), "
        "generated reports and approved submissions (backfill, no OCR). --document / --report / --submission <id> "
        "re-reads one file (scans included), replacing its chunks."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--document", type=int, help="Re-index only this document ID (reads scans with AI).")
        parser.add_argument("--report", type=int, help="Re-index only this generated report ID.")
        parser.add_argument("--submission", type=int, help="Re-index only this submission ID.")

    def _line(self, result: dict) -> str:
        line = f"{result['kind'].title()} {result['id']}: {result['status']}"
        if result["chunks"]:
            line += f", {result['chunks']} chunks"
        if result["reason"]:
            line += f" - {result['reason']}"
        return line if line.endswith(".") else line + "."

    def handle(self, *args, **options) -> None:
        one = [(kind, options[option]) for kind, option in
               ((indexing.DOCUMENT, "document"), (indexing.REPORT, "report"), (indexing.SUBMISSION, "submission"))
               if options[option]]
        if one:
            for kind, pk in one:
                result = indexing.index_source(kind, pk)
                style = self.style.SUCCESS if result["status"] == indexing.INDEXED else self.style.WARNING
                self.stdout.write(style(self._line(result)))
            return

        pending = indexing.pending_sources()
        if not pending:
            self.stdout.write(self.style.SUCCESS("Nothing to index: every file that can be indexed already is."))
            return
        counts = {indexing.INDEXED: 0, indexing.NOT_INDEXABLE: 0, indexing.FAILED: 0}
        for kind, pk in pending:
            result = indexing.index_source(kind, pk, mode=indexing.FULL, allow_ocr=False)
            counts[result["status"]] += 1
            self.stdout.write(f"  {self._line(result)}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed: {counts[indexing.INDEXED]}, not indexable: {counts[indexing.NOT_INDEXABLE]}, "
                f"failed: {counts[indexing.FAILED]} (failed ones are retried by the next run; scans are read with "
                "--document <id>)."
            )
        )
