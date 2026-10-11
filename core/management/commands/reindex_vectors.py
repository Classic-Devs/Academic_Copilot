"""
Rebuild the vector index from the chunks stored in the database.

Needed after changing the embedding model or EMBEDDING_DIMENSIONS, and once when
upgrading from the legacy `academic_docs` collection (which lacked per-user
metadata and used a different embedding size). Chunk text is re-embedded in
batches; documents that are already fully indexed are skipped unless --force.
"""

from django.core.management.base import BaseCommand

from core.ai_service import get_ai_service
from core.models import AcademicDocument
from core.processor import EMBED_BATCH, _metadata

LEGACY_COLLECTION = "academic_docs"


class Command(BaseCommand):
    help = "Re-embed stored chunks into the current vector collection."

    def add_arguments(self, parser):
        parser.add_argument("--document", type=int, help="Only reindex this document id.")
        parser.add_argument("--force", action="store_true", help="Reindex even if already indexed.")
        parser.add_argument("--drop-legacy", action="store_true", help=f"Delete the old '{LEGACY_COLLECTION}' collection afterwards.")

    def handle(self, *args, **opts):
        service = get_ai_service()
        vectors = service.vectors

        docs = AcademicDocument.objects.filter(chunks__isnull=False).distinct().order_by("pk")
        if opts["document"]:
            docs = docs.filter(pk=opts["document"])

        # Identical files share one embedding pass: {file_hash: {(page, idx): vector}}.
        by_hash = {}
        done = skipped = failed = 0

        for doc in docs.iterator():
            chunks = list(doc.chunks.order_by("pk"))
            if not opts["force"] and len(vectors.get_embeddings([c.pk for c in chunks])) == len(chunks):
                skipped += 1
                continue
            try:
                cached = by_hash.get(doc.file_hash) if doc.file_hash else None
                if cached and all((c.page_number, c.chunk_index) in cached for c in chunks):
                    embeddings = [cached[(c.page_number, c.chunk_index)] for c in chunks]
                else:
                    embeddings = []
                    for start in range(0, len(chunks), EMBED_BATCH):
                        batch = [c.content for c in chunks[start:start + EMBED_BATCH]]
                        embeddings.extend(service.embeddings.embed_documents(batch, title=doc.title))
                    if doc.file_hash:
                        by_hash[doc.file_hash] = {
                            (c.page_number, c.chunk_index): e for c, e in zip(chunks, embeddings)
                        }

                vectors.delete_document(doc.pk)
                vectors.add(
                    ids=[c.pk for c in chunks],
                    embeddings=embeddings,
                    metadatas=[_metadata(doc, c) for c in chunks],
                )
                for c in chunks:
                    c.vector_id = str(c.pk)
                doc.chunks.model.objects.bulk_update(chunks, ["vector_id"])
                done += 1
                self.stdout.write(f"  indexed #{doc.pk} {doc.title} ({len(chunks)} chunks)")
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  FAILED #{doc.pk} {doc.title}: {exc}")

        self.stdout.write(self.style.SUCCESS(
            f"Reindex complete: {done} indexed, {skipped} already current, {failed} failed. "
            f"Collection now holds {vectors.count()} vectors."
        ))

        if opts["drop_legacy"]:
            try:
                vectors.client.delete_collection(LEGACY_COLLECTION)
                self.stdout.write(f"Dropped legacy collection '{LEGACY_COLLECTION}'.")
            except Exception:
                self.stdout.write(f"No legacy collection '{LEGACY_COLLECTION}' to drop.")
