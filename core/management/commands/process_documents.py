"""
Process queued documents synchronously.

Use it from a scheduler (cron / Cloud Scheduler / Render cron job) to pick up
work that an instance lost when it was recycled mid-job, or to retry failures.
"""

from django.core.management.base import BaseCommand

from core.models import AcademicDocument
from core.processor import process_document, recover_stale_documents


class Command(BaseCommand):
    help = "Recover stale jobs and process pending documents."

    def add_arguments(self, parser):
        parser.add_argument("--retry-errors", action="store_true", help="Also retry documents in the error state.")
        parser.add_argument("--limit", type=int, default=50, help="Maximum documents to process in this run.")

    def handle(self, *args, **opts):
        statuses = ["pending"]
        if opts["retry_errors"]:
            statuses.append("error")

        recovered = recover_stale_documents()
        if recovered:
            self.stdout.write(f"Marked {recovered} stale document(s) as interrupted.")
            if "error" not in statuses:
                statuses.append("error")

        ids = list(
            AcademicDocument.objects.filter(status__in=statuses)
            .order_by("updated_at").values_list("pk", flat=True)[: opts["limit"]]
        )
        for pk in ids:
            process_document(pk)
            doc = AcademicDocument.objects.get(pk=pk)
            self.stdout.write(f"  #{pk} {doc.title}: {doc.status}" + (f" ({doc.error_message})" if doc.error_message else ""))
        self.stdout.write(self.style.SUCCESS(f"Processed {len(ids)} document(s)."))
