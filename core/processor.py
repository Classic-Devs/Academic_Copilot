"""
Document ingestion: extract → chunk → embed → index.

Jobs run on a small, bounded thread pool and are submitted only after the
surrounding database transaction commits. Progress bumps `updated_at`, so a job
that dies with its process is detected as stale and can be retried.
"""

import io
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pypdf
from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from .ai_service import friendly_error, get_ai_service
from .chunking import normalize_text, split_text
from .models import AcademicDocument, DocumentChunk

logger = logging.getLogger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
EMBED_BATCH = 50
VECTOR_WRITE_BATCH = 500

TEXT_EXTENSIONS = {".txt", ".md"}
SUPPORTED_EXTENSIONS = {".pdf"} | TEXT_EXTENSIONS


class ProcessingError(Exception):
    """A document-level failure with a user-safe message."""


_executor = None
_executor_lock = threading.Lock()
_active_ids = set()
_active_lock = threading.Lock()


def _get_executor():
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                _executor = ThreadPoolExecutor(
                    max_workers=max(1, settings.DOCUMENT_PROCESSING_WORKERS),
                    thread_name_prefix="docproc",
                )
    return _executor


def enqueue_document(document_id):
    """Schedule processing once the current transaction (if any) commits."""
    transaction.on_commit(lambda: _get_executor().submit(_run_job, document_id))


def _run_job(document_id):
    close_old_connections()
    try:
        process_document(document_id)
    except Exception:  # never let a worker thread die silently
        logger.exception("Unhandled error processing document %s", document_id)
    finally:
        close_old_connections()


def is_active(document_id):
    with _active_lock:
        return document_id in _active_ids


def recover_stale_documents(user=None):
    """Mark documents whose processing stopped making progress as failed (retryable)."""
    cutoff = timezone.now() - timedelta(minutes=settings.PROCESSING_STALE_MINUTES)
    qs = AcademicDocument.objects.filter(status__in=("processing", "pending"), updated_at__lt=cutoff)
    if user is not None:
        qs = qs.filter(user=user)
    with _active_lock:
        active = set(_active_ids)
    stale_ids = [pk for pk in qs.values_list("pk", flat=True) if pk not in active]
    if stale_ids:
        AcademicDocument.objects.filter(pk__in=stale_ids).update(
            status="error",
            error_message="Processing was interrupted. Click retry to process it again.",
            updated_at=timezone.now(),
        )
    return len(stale_ids)


# ── Extraction ───────────────────────────────────────────────────────────────

def file_extension(name):
    dot = name.rfind(".")
    return name[dot:].lower() if dot != -1 else ""


def extract_pages(document):
    """Return [(page_number, text)] for the document's file (any storage backend)."""
    ext = file_extension(document.file.name)
    with document.file.open("rb") as f:
        data = f.read()

    if ext in TEXT_EXTENSIONS:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        return [(1, normalize_text(text))]

    if ext != ".pdf":
        raise ProcessingError("Unsupported file type.")

    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            raise ProcessingError("This PDF is password-protected. Upload an unlocked copy.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = normalize_text(page.extract_text() or "")
            if text:
                pages.append((number, text))
        return pages
    except ProcessingError:
        raise
    except Exception as exc:
        logger.warning("PDF parse failure for document %s: %s", document.pk, exc)
        raise ProcessingError("This PDF could not be read; it may be corrupted.") from exc


def build_chunks(pages):
    """[(page_number, chunk_index, text)] for every page."""
    out = []
    for page_number, text in pages:
        for index, chunk in enumerate(split_text(text, CHUNK_SIZE, CHUNK_OVERLAP)):
            out.append((page_number, index, chunk))
    return out


# ── Indexing ─────────────────────────────────────────────────────────────────

def _metadata(document, chunk):
    return {
        "user_id": int(document.user_id or 0),
        "document_id": int(document.pk),
        "page": int(chunk.page_number or 0),
        "chunk_index": int(chunk.chunk_index),
    }


def _touch(document):
    AcademicDocument.objects.filter(pk=document.pk).update(updated_at=timezone.now())


def _clear_index(document, vectors):
    vectors.delete_document(document.pk)
    DocumentChunk.objects.filter(document=document).delete()


def _store(document, rows, embeddings, vectors):
    """Persist chunk rows and their vectors. rows: [(page, index, text)]."""
    with transaction.atomic():
        created = DocumentChunk.objects.bulk_create(
            DocumentChunk(document=document, page_number=p, chunk_index=i, content=t) for p, i, t in rows
        )
        for chunk in created:
            chunk.vector_id = str(chunk.pk)
        DocumentChunk.objects.bulk_update(created, ["vector_id"])

    for start in range(0, len(created), VECTOR_WRITE_BATCH):
        batch = created[start:start + VECTOR_WRITE_BATCH]
        vectors.add(
            ids=[c.pk for c in batch],
            embeddings=embeddings[start:start + VECTOR_WRITE_BATCH],
            metadatas=[_metadata(document, c) for c in batch],
        )


def copy_index(source, target, vectors=None):
    """
    Give `target` the chunks and vectors of `source` without calling the
    embedding API. Returns False if the source index is incomplete or was built
    with a different embedding size.
    """
    vectors = vectors or get_ai_service().vectors
    src_chunks = list(source.chunks.order_by("pk"))
    if not src_chunks:
        return False
    found = vectors.get_embeddings([c.pk for c in src_chunks])
    if len(found) != len(src_chunks):
        return False
    embeddings = [found[str(c.pk)] for c in src_chunks]
    if any(len(e) != settings.EMBEDDING_DIMENSIONS for e in embeddings):
        return False

    _clear_index(target, vectors)
    rows = [(c.page_number, c.chunk_index, c.content) for c in src_chunks]
    _store(target, rows, embeddings, vectors)
    return True


def _find_index_donor(document):
    if not document.file_hash:
        return None
    return (
        AcademicDocument.objects.filter(file_hash=document.file_hash, status="ready")
        .exclude(pk=document.pk)
        .order_by("-updated_at")
        .first()
    )


def process_document(document_id):
    with _active_lock:
        if document_id in _active_ids:
            return
        _active_ids.add(document_id)
    try:
        _process(document_id)
    finally:
        with _active_lock:
            _active_ids.discard(document_id)


def _process(document_id):
    try:
        document = AcademicDocument.objects.get(pk=document_id)
    except AcademicDocument.DoesNotExist:
        return

    AcademicDocument.objects.filter(pk=document_id).update(
        status="processing", error_message=None, updated_at=timezone.now()
    )
    service = get_ai_service()
    vectors = service.vectors

    try:
        donor = _find_index_donor(document)
        if donor and copy_index(donor, document, vectors):
            logger.info("Doc %s: reused index of identical doc %s", document_id, donor.pk)
        else:
            rows = build_chunks(extract_pages(document))
            if not rows:
                raise ProcessingError(
                    "No extractable text was found. Scanned/image-only PDFs are not supported yet."
                )
            if len(rows) > settings.MAX_CHUNKS_PER_DOCUMENT:
                raise ProcessingError(
                    f"Document is too large ({len(rows)} sections; limit {settings.MAX_CHUNKS_PER_DOCUMENT})."
                )

            embeddings = []
            for start in range(0, len(rows), EMBED_BATCH):
                batch = [t for _, _, t in rows[start:start + EMBED_BATCH]]
                embeddings.extend(service.embeddings.embed_documents(batch, title=document.title))
                _touch(document)
                logger.info("Doc %s: embedded %d/%d chunks", document_id, len(embeddings), len(rows))

            _clear_index(document, vectors)
            _store(document, rows, embeddings, vectors)

        AcademicDocument.objects.filter(pk=document_id).update(
            status="ready", error_message=None, updated_at=timezone.now()
        )
    except Exception as exc:
        if isinstance(exc, ProcessingError):
            message = str(exc)
        else:
            logger.exception("Error processing document %s", document_id)
            message = friendly_error(exc)
        try:
            _clear_index(document, vectors)
        except Exception:
            logger.exception("Could not clean up partial index for document %s", document_id)
        AcademicDocument.objects.filter(pk=document_id).update(
            status="error", error_message=message, updated_at=timezone.now()
        )
