# AltaStudy — Academic Copilot

AltaStudy is a context-aware study assistant. Students upload their own course material (lecture notes, past papers, textbooks) and ask questions in plain language; answers are grounded in, and cite, their own documents using **Retrieval-Augmented Generation (RAG)**. A community library lets students share resources and add others' public uploads to their own knowledge base.

It was built as a GDG (Google Developer Groups) workshop flagship, showcasing modern AI-engineering patterns in a practical, student-facing product.

---

## How it works

| Layer | What happens | Implementation |
|---|---|---|
| **1. Ingestion** | Upload is validated (type, size, file signature), hashed (SHA-256) and stored privately | `core/security.py`, `core/views.py` |
| **2. Processing** | Text is extracted per page, normalised, and split with recursive character chunking (1000 chars, 200 overlap) | `core/processor.py`, `core/chunking.py` |
| **3. Embedding** | Chunks are embedded in batches with `gemini-embedding-001` at 768 dimensions (L2-normalised) | `core/ai_service.py` |
| **4. Storage** | Vectors go to ChromaDB (cosine HNSW index) tagged with `user_id` / `document_id`; chunk text and metadata live in the relational DB | `VectorStore` |
| **5. Retrieval** | The question is embedded (cached), searched **only within the user's own vectors**, filtered by a calibrated similarity floor, and de-duplicated | `AIService.retrieve` |
| **6. Generation** | Retrieved passages + the last 3 turns are sent to Gemini; the answer **streams** to the browser with source citations and a confidence indicator | `AIService.stream_answer`, `ChatView` |

### Efficiency features
- **Per-user vector filtering** inside the ANN search (no global top-k then discard), so results never depend on other users' data.
- **Content-hash deduplication** — identical files (re-uploads, or resources added from the community library) reuse existing vectors: zero embedding calls, and the stored file is shared rather than copied.
- **768-dim Matryoshka embeddings** — ~4× smaller index and faster search than the 3072-dim default with negligible quality loss.
- **Bulk DB writes** (`bulk_create`) and batched vector upserts; a single process-wide AI client.
- **Query-embedding cache** for repeated questions.
- **Bounded worker pool** for ingestion, with progress heartbeats and automatic recovery of interrupted jobs.

### Supported files
PDF (text-based), `.txt` and `.md`, up to `MAX_UPLOAD_SIZE_MB` (default 25 MB). Scanned/image-only PDFs are reported as unsupported (OCR is on the roadmap).

---

## Tech stack

| Component | Technology |
|---|---|
| Backend | Django 6 (Python 3.12), Gunicorn (gthread) |
| AI SDK | `google-genai` |
| Embeddings | `gemini-embedding-001` (768 dims) |
| Generation | `gemini-3.1-flash-lite-preview` (configurable) |
| Vector DB | ChromaDB — local persistent, or remote server via `CHROMA_HOST` |
| Relational DB | SQLite (dev) / PostgreSQL via `DATABASE_URL` |
| File storage | Local disk (dev) / private Google Cloud Storage bucket |
| PDF parsing | pypdf |
| Frontend | Django templates, Tailwind, marked + DOMPurify |

---

## Local development

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cat > .env <<'EOF'
DEBUG=True
GEMINI_API_KEY=your-key
EOF

python manage.py migrate
python manage.py ai_healthcheck      # verifies Gemini + vector store
python manage.py runserver
```

Run the tests (the AI layer is faked, so no API key or quota is used):

```bash
python manage.py test core
```

---

## Configuration

All settings come from environment variables (or `.env`).

| Variable | Default | Purpose |
|---|---|---|
| `DEBUG` | `False` | Never enable in production |
| `SECRET_KEY` | — | **Required** when `DEBUG=False` |
| `ALLOWED_HOSTS` | localhost, `.onrender.com`, `.appspot.com`, `.run.app` | Comma-separated |
| `CSRF_TRUSTED_ORIGINS` | `https://*.onrender.com`, … | Comma-separated |
| `GEMINI_API_KEY` | — | Gemini API key |
| `GEMINI_CHAT_MODEL` | `gemini-3.1-flash-lite-preview` | Generation model |
| `GEMINI_EMBEDDING_MODEL` | `gemini-embedding-001` | Embedding model |
| `EMBEDDING_DIMENSIONS` | `768` | Changing it requires `reindex_vectors` |
| `DATABASE_URL` | SQLite | e.g. Postgres URL |
| `GS_BUCKET_NAME` | — | Store uploads in a private GCS bucket |
| `CHROMA_HOST` / `CHROMA_PORT` / `CHROMA_SSL` | — / 8000 / False | Remote Chroma server |
| `CHROMA_PATH` | `./chroma_db` | Local Chroma directory |
| `CHROMA_COLLECTION` | `academic_chunks` | Collection name |
| `CACHE_URL` | in-memory | e.g. `redis://…` to share rate limits across instances |
| `TRUSTED_PROXY_COUNT` | `0` | Proxies appending to `X-Forwarded-For` (Render: 1, Google front end: 2) |
| `MAX_UPLOAD_SIZE_MB` | `25` | Per-file upload limit |
| `MAX_CHUNKS_PER_DOCUMENT` | `3000` | Guards against runaway ingestion cost |
| `DOCUMENT_PROCESSING_WORKERS` | `2` | Ingestion threads per process |
| `PROCESSING_STALE_MINUTES` | `20` | Silence after which a job counts as interrupted |
| `RATE_LIMIT_LOGIN` / `_SIGNUP` / `_CHAT` / `_UPLOAD` / `_ACQUIRE` | `10/300`, `5/3600`, `20/60`, `40/3600`, `30/3600` | `count/seconds` |

---

## Operations

| Command | Use |
|---|---|
| `python manage.py ai_healthcheck [--list-models]` | Check Gemini and vector-store connectivity |
| `python manage.py reindex_vectors [--document ID] [--force] [--drop-legacy]` | Rebuild the vector index from stored chunks (after changing model/dimensions; `--drop-legacy` removes the pre-upgrade `academic_docs` collection) |
| `python manage.py process_documents [--retry-errors]` | Recover interrupted jobs and process pending documents — schedule it (cron / Cloud Scheduler) in production |

Admins can also re-process documents from the Django admin (`Re-process selected documents` action).

---

## Deployment

**Docker / Cloud Run / Render** — the `Dockerfile` runs as a non-root user; `entrypoint.sh` applies migrations and collects static files. Set `SECRET_KEY`, `GEMINI_API_KEY`, `DATABASE_URL` and, for persistent storage, either mount volumes for `media/` and `chroma_db/` or use `GS_BUCKET_NAME` + `CHROMA_HOST`.

**App Engine Standard** — see `app.yaml`. The filesystem is read-only, so `GS_BUCKET_NAME`, `CHROMA_HOST` and `DATABASE_URL` are required.

> Local Chroma (`PersistentClient`) is single-node. With several instances or workers writing at once, run a Chroma server and set `CHROMA_HOST`.

---

## Security

- Secrets and `DEBUG` come from the environment; the app refuses to start without `SECRET_KEY` in production. HTTPS redirect, HSTS, secure/HttpOnly cookies, `nosniff`, strict referrer policy and `X-Frame-Options: DENY` are enabled outside `DEBUG`.
- Uploaded files are **never** publicly addressable: stored under random names, served only through a view that checks ownership or public visibility.
- Every document/chat action is scoped to the requesting user; retrieval is filtered by user both in the vector search and in the database.
- Uploads are validated by extension, size and file signature; titles are sanitised.
- Rendered AI output is sanitised with DOMPurify; user-controlled strings are inserted with `textContent`; chat history is embedded via `json_script`; CDN scripts are pinned with Subresource Integrity.
- Retrieved document text is fenced as data in the prompt and the model is instructed to ignore instructions inside it (prompt-injection mitigation).
- Rate limiting on login, sign-up, chat, upload and acquire; logout is POST-only (CSRF-protected); login redirects are restricted to the same host.
- Errors shown to users are generic; details go to server logs.

---

## Roadmap

- **Multi-modal input** — OCR / vision embeddings for scanned PDFs, photos of handwritten notes and whiteboards; PowerPoint and Word support.
- **Deep source linking** — open the PDF at the cited page with the passage highlighted.
- **Collaborative knowledge pools** — permissioned shared libraries for a chapter, class or department.
- **Study analytics** — frequently asked topics, knowledge gaps and pre-exam priority lists.
- **Durable task queue** — Cloud Tasks / Celery for ingestion at larger scale.
