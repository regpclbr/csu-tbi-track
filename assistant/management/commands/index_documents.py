"""Index approved documents for the assistant, by hand. Safe to run again."""

from django.core.management.base import BaseCommand

from assistant import indexing


class Command(BaseCommand):
    help = (
        "Index every approved activity document that has no embeddings yet (backfill). "
        "--document <id> re-indexes one document, replacing its chunks."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument("--document", type=int, help="Re-index only this document ID.")

    def handle(self, *args, **options) -> None:
        if options["document"]:
            result = indexing.index_document(options["document"])
            line = f"Document {result['document_id']}: {result['status']}"
            if result["chunks"]:
                line += f", {result['chunks']} chunks"
            if result["reason"]:
                line += f" - {result['reason']}"
            style = self.style.SUCCESS if result["status"] == indexing.INDEXED else self.style.WARNING
            self.stdout.write(style(line if line.endswith(".") else line + "."))
            return

        pending = list(indexing.pending_documents().select_related("document_type"))
        if not pending:
            self.stdout.write(self.style.SUCCESS("Nothing to index: every approved document that can be indexed already is."))
            return
        counts = {indexing.INDEXED: 0, indexing.NOT_INDEXABLE: 0, indexing.FAILED: 0}
        for document in pending:
            result = indexing.index_document(document.pk)
            counts[result["status"]] += 1
            detail = f"{result['chunks']} chunks" if result["status"] == indexing.INDEXED else result["reason"]
            self.stdout.write(f"  Document {document.pk} ({document.document_type.name}, {document.title}): {result['status']} - {detail}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Indexed: {counts[indexing.INDEXED]}, not indexable: {counts[indexing.NOT_INDEXABLE]}, "
                f"failed: {counts[indexing.FAILED]} (failed ones are retried by the next run)."
            )
        )
