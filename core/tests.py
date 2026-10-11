import hashlib
import json
import math
import shutil
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core import ai_service
from core.ai_service import AIService, ChatManager, Retrieval
from core.chunking import normalize_text, split_text
from core.models import AcademicDocument, ChatMessage, DocumentChunk
from core.processor import process_document, recover_stale_documents
from core.security import UploadRejected, clean_title, rate_limited, validate_upload

DIM = 16


# ── Test doubles ─────────────────────────────────────────────────────────────

def fake_embed(text):
    """Deterministic bag-of-words embedding: texts sharing words are similar."""
    vec = [0.0] * DIM
    for word in text.lower().split():
        vec[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class FakeVectors:
    def __init__(self):
        self.rows = {}  # id -> (embedding, metadata)

    def add(self, ids, embeddings, metadatas):
        for i, e, m in zip(ids, embeddings, metadatas):
            self.rows[str(i)] = (list(e), dict(m))

    def get_embeddings(self, ids):
        return {str(i): self.rows[str(i)][0] for i in ids if str(i) in self.rows}

    def query(self, embedding, user_id, n_results):
        scored = [
            (cid, sum(a * b for a, b in zip(embedding, emb)))
            for cid, (emb, meta) in self.rows.items() if meta["user_id"] == user_id
        ]
        scored.sort(key=lambda x: -x[1])
        return scored[:n_results]

    def delete_document(self, document_id):
        self.rows = {k: v for k, v in self.rows.items() if v[1]["document_id"] != document_id}

    def count(self):
        return len(self.rows)


class FakeEmbeddings:
    def __init__(self):
        self.calls = 0

    def embed_documents(self, texts, title=None):
        self.calls += 1
        return [fake_embed(t) for t in texts]

    def embed_query(self, text):
        return fake_embed(text)


class FakeChat:
    def __init__(self):
        self.prompts = []

    def stream(self, prompt, system_instruction):
        self.prompts.append(prompt)
        yield "Polymorphism "
        yield "lets objects take many forms."


def make_fake_service():
    service = AIService.__new__(AIService)
    service.embeddings = FakeEmbeddings()
    service.vectors = FakeVectors()
    service.chat = FakeChat()
    return service


def run_inline(pk):
    process_document(pk)


TEXT = (
    "Polymorphism in object oriented programming allows a single interface to represent "
    "different underlying forms. Inheritance lets a class reuse behaviour from a parent class.\n\n"
    "Encapsulation hides internal state behind methods."
)


@override_settings(EMBEDDING_DIMENSIONS=DIM, RATE_LIMITS={})
class AppTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.mkdtemp()
        media_override = override_settings(MEDIA_ROOT=self.media)
        media_override.enable()
        self.addCleanup(media_override.disable)
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)

        self.service = make_fake_service()
        ai_service._service = self.service
        self.addCleanup(ai_service.reset_ai_service)

        patcher = mock.patch("core.views.enqueue_document", side_effect=run_inline)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.alice = User.objects.create_user("alice", "alice@example.com", "s3cure-Passw0rd")
        self.bob = User.objects.create_user("bob", "bob@example.com", "s3cure-Passw0rd")

    def upload(self, user, name="notes.txt", content=TEXT, public="true"):
        self.client.force_login(user)
        data = content.encode() if isinstance(content, str) else content
        return self.client.post(reverse("upload"), {
            "file": SimpleUploadedFile(name, data), "field_of_study": "CS", "is_public": public,
        })


# ── Chunking ─────────────────────────────────────────────────────────────────

class ChunkingTests(SimpleTestCase):
    def test_short_text_is_single_chunk(self):
        self.assertEqual(split_text("hello world", 100, 20), ["hello world"])

    def test_empty_text(self):
        self.assertEqual(split_text("   ", 100, 20), [])

    def test_chunks_respect_size_and_cover_all_words(self):
        words = [f"word{i}" for i in range(2000)]
        text = " ".join(words)
        chunks = split_text(text, chunk_size=300, overlap=50)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 300 + 50 + 1)
        joined = " ".join(chunks)
        for w in words:
            self.assertIn(w, joined)

    def test_overlap_carries_previous_context(self):
        text = "\n\n".join(f"Paragraph {i} " + "x" * 80 for i in range(10))
        chunks = split_text(text, chunk_size=200, overlap=40)
        for prev, cur in zip(chunks, chunks[1:]):
            self.assertTrue(any(tok in cur for tok in prev.split()[-1:]))

    def test_repeated_paragraphs_are_not_dropped(self):
        # The previous implementation compared parts by value and mishandled repeats.
        text = "\n\n".join(["same paragraph " * 5] * 6)
        chunks = split_text(text, chunk_size=200, overlap=0)
        self.assertEqual(sum(c.count("same paragraph") for c in chunks), 30)

    def test_invalid_overlap(self):
        with self.assertRaises(ValueError):
            split_text("abc", 10, 10)

    def test_normalize_rejoins_hyphenation_and_collapses_space(self):
        self.assertEqual(normalize_text("inheri-\ntance   is\t\tgood\n\n\n\nnext"), "inheritance is good\n\nnext")


# ── Security helpers ─────────────────────────────────────────────────────────

class SecurityHelperTests(SimpleTestCase):
    def test_rejects_fake_pdf(self):
        with self.assertRaises(UploadRejected):
            validate_upload(SimpleUploadedFile("x.pdf", b"<html>not a pdf</html>"))

    def test_rejects_disallowed_extension(self):
        with self.assertRaises(UploadRejected):
            validate_upload(SimpleUploadedFile("x.exe", b"MZ..."))

    @override_settings(MAX_UPLOAD_SIZE_MB=0)
    def test_rejects_oversized(self):
        with self.assertRaises(UploadRejected):
            validate_upload(SimpleUploadedFile("x.txt", b"hello"))

    def test_accepts_pdf_and_returns_hash(self):
        data = b"%PDF-1.7\n..."
        self.assertEqual(validate_upload(SimpleUploadedFile("x.pdf", data)), hashlib.sha256(data).hexdigest())

    def test_clean_title_strips_paths_and_markup(self):
        self.assertEqual(clean_title('..\\..\\evil<script>".pdf'), "evilscript.pdf")

    @override_settings(RATE_LIMITS={"chat": "3/60"})
    def test_rate_limit(self):
        cache.clear()
        results = [rate_limited("chat", "u1") for _ in range(4)]
        self.assertEqual(results, [False, False, False, True])
        self.assertFalse(rate_limited("chat", "u2"))


# ── Ingestion & library ──────────────────────────────────────────────────────

class LibraryTests(AppTestCase):
    def test_login_required(self):
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("login"), response["Location"])

    def test_upload_processes_and_indexes_with_user_metadata(self):
        response = self.upload(self.alice)
        self.assertEqual(response.status_code, 200, response.content)
        doc = AcademicDocument.objects.get(user=self.alice)
        self.assertEqual(doc.status, "ready", doc.error_message)
        self.assertTrue(doc.file_hash)
        self.assertGreater(doc.chunks.count(), 0)
        metas = [m for _, m in self.service.vectors.rows.values()]
        self.assertTrue(all(m["user_id"] == self.alice.pk and m["document_id"] == doc.pk for m in metas))

    def test_duplicate_content_rejected(self):
        self.upload(self.alice)
        response = self.upload(self.alice, name="renamed.txt")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(AcademicDocument.objects.filter(user=self.alice).count(), 1)

    def test_invalid_pdf_rejected(self):
        response = self.upload(self.alice, name="bad.pdf", content="not a pdf")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AcademicDocument.objects.exists())

    def test_other_users_cannot_modify_documents(self):
        self.upload(self.alice)
        doc = AcademicDocument.objects.get(user=self.alice)
        self.client.force_login(self.bob)
        for name in ("delete_document", "reprocess_document", "toggle_visibility"):
            self.assertEqual(self.client.post(reverse(name, args=[doc.pk])).status_code, 404, name)
        self.assertTrue(AcademicDocument.objects.filter(pk=doc.pk).exists())

    def test_file_access_respects_visibility(self):
        self.upload(self.alice, public="false")
        doc = AcademicDocument.objects.get(user=self.alice)
        url = reverse("document_file", args=[doc.pk])

        self.client.force_login(self.alice)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), TEXT.encode())

        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(url).status_code, 404)

        doc.is_public = True
        doc.save()
        self.assertEqual(self.client.get(url).status_code, 200)

    def test_acquire_reuses_file_and_vectors_without_embedding(self):
        self.upload(self.alice)
        original = AcademicDocument.objects.get(user=self.alice)
        calls_before = self.service.embeddings.calls

        self.client.force_login(self.bob)
        response = self.client.post(reverse("acquire_document", args=[original.pk]))
        self.assertEqual(response.status_code, 200, response.content)

        copy = AcademicDocument.objects.get(user=self.bob)
        self.assertEqual(copy.status, "ready", copy.error_message)
        self.assertEqual(copy.file.name, original.file.name)
        self.assertEqual(copy.chunks.count(), original.chunks.count())
        self.assertEqual(self.service.embeddings.calls, calls_before)
        self.assertFalse(copy.is_public)

        # Acquired copies cannot be re-published, and can't be acquired twice.
        self.assertEqual(self.client.post(reverse("toggle_visibility", args=[copy.pk])).status_code, 403)
        self.assertEqual(self.client.post(reverse("acquire_document", args=[original.pk])).status_code, 400)

    def test_private_documents_cannot_be_acquired(self):
        self.upload(self.alice, public="false")
        doc = AcademicDocument.objects.get(user=self.alice)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(reverse("acquire_document", args=[doc.pk])).status_code, 404)

    def test_delete_keeps_file_shared_by_acquired_copy(self):
        self.upload(self.alice)
        original = AcademicDocument.objects.get(user=self.alice)
        self.client.force_login(self.bob)
        self.client.post(reverse("acquire_document", args=[original.pk]))

        self.client.force_login(self.alice)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(reverse("delete_document", args=[original.pk]))
        self.assertEqual(response.status_code, 200)
        copy = AcademicDocument.objects.get(user=self.bob)
        self.assertTrue(copy.file.storage.exists(copy.file.name))
        metas = [m for _, m in self.service.vectors.rows.values()]
        self.assertTrue(metas and all(m["document_id"] == copy.pk for m in metas))

    def test_global_resources_lists_only_others_public_ready_docs(self):
        self.upload(self.alice)
        self.upload(self.alice, name="private.txt", content="secret notes about cryptography", public="false")
        self.client.force_login(self.bob)
        response = self.client.get(reverse("global_resources"))
        titles = [d.title for d in response.context["docs"]]
        self.assertEqual(titles, ["notes.txt"])

    def test_dashboard_and_status_endpoint(self):
        self.upload(self.alice)
        response = self.client.get(reverse("dashboard"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["total_docs"], 1)
        self.assertEqual(response.context["ai_confidence"], "—")
        status = self.client.get(reverse("document_status")).json()
        self.assertEqual(status["documents"][0]["status"], "ready")

    def test_stale_processing_is_recovered(self):
        doc = AcademicDocument.objects.create(user=self.alice, title="t", file="academic_docs/x.txt", status="processing")
        AcademicDocument.objects.filter(pk=doc.pk).update(updated_at=timezone.now() - timedelta(hours=2))
        self.assertEqual(recover_stale_documents(), 1)
        doc.refresh_from_db()
        self.assertEqual(doc.status, "error")

    def test_scanned_or_empty_document_reports_error(self):
        self.upload(self.alice, name="blank.txt", content="   \n  ")
        doc = AcademicDocument.objects.get(user=self.alice)
        self.assertEqual(doc.status, "error")
        self.assertIn("No extractable text", doc.error_message)


# ── Retrieval & chat ─────────────────────────────────────────────────────────

class ChatTests(AppTestCase):
    def ask(self, query):
        response = self.client.post(reverse("chat"), json.dumps({"query": query}), content_type="application/json")
        if response.status_code != 200:
            return response, []
        events = [json.loads(line) for line in b"".join(response.streaming_content).decode().splitlines() if line]
        return response, events

    def test_retrieval_is_scoped_to_user(self):
        self.upload(self.alice)
        retrieval = self.service.retrieve(TEXT, self.bob)
        self.assertEqual(retrieval.chunks, [])
        retrieval = self.service.retrieve(TEXT, self.alice)
        self.assertTrue(retrieval.chunks)
        self.assertIsNotNone(retrieval.confidence)

    def test_chat_streams_and_saves_message(self):
        self.upload(self.alice)
        response, events = self.ask(TEXT)
        self.assertEqual(response["Content-Type"], "application/x-ndjson")
        types_ = [e["type"] for e in events]
        self.assertEqual(types_[0], "meta")
        self.assertEqual(types_[-1], "done")
        self.assertTrue(events[0]["citations"])

        msg = ChatMessage.objects.get(user=self.alice)
        self.assertEqual(msg.response, "Polymorphism lets objects take many forms.")
        self.assertIsNotNone(msg.confidence)
        self.assertEqual(events[-1]["id"], msg.pk)
        # Retrieved text is fenced as data in the prompt.
        self.assertIn("<context>", self.service.chat.prompts[0])

    def test_chat_validates_input(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.ask("")[0].status_code, 400)
        self.assertEqual(self.ask("x" * 5000)[0].status_code, 400)
        bad = self.client.post(reverse("chat"), "not json", content_type="application/json")
        self.assertEqual(bad.status_code, 400)

    @override_settings(RATE_LIMITS={"chat": "1/60"})
    def test_chat_rate_limited(self):
        self.client.force_login(self.alice)
        self.ask("first question")
        self.assertEqual(self.ask("second question")[0].status_code, 429)

    def test_history_is_safely_embedded(self):
        ChatMessage.objects.create(user=self.alice, query="</script><script>alert(1)</script>", response="ok")
        self.client.force_login(self.alice)
        html = self.client.get(reverse("chat")).content.decode()
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn('id="chat-history"', html)

    def test_history_shows_latest_messages(self):
        for i in range(55):
            ChatMessage.objects.create(user=self.alice, query=f"q{i}", response="r")
        self.client.force_login(self.alice)
        history = self.client.get(reverse("chat")).context["chat_history"]
        self.assertEqual(len(history), 50)
        self.assertEqual(history[-1]["query"], "q54")
        self.assertEqual(history[0]["query"], "q5")

    def test_users_cannot_delete_others_messages(self):
        msg = ChatMessage.objects.create(user=self.alice, query="q", response="r")
        self.client.force_login(self.bob)
        self.assertEqual(self.client.post(reverse("delete_chat_message", args=[msg.pk])).status_code, 404)


class ChatManagerStreamTests(SimpleTestCase):
    def test_stream_yields_all_text_including_first_chunk(self):
        chunks = [mock.Mock(text="a"), mock.Mock(text=None), mock.Mock(text="b")]
        client = mock.Mock()
        client.models.generate_content_stream.return_value = iter(chunks)
        manager = ChatManager.__new__(ChatManager)
        manager.client, manager.model = client, "m"
        self.assertEqual("".join(manager.stream("p", "s")), "ab")

    def test_retrieval_confidence_empty(self):
        self.assertIsNone(Retrieval().confidence)


# ── Auth ─────────────────────────────────────────────────────────────────────

class AuthTests(AppTestCase):
    def test_logout_requires_post(self):
        self.client.force_login(self.alice)
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)
        self.assertEqual(self.client.post(reverse("logout")).status_code, 302)

    def test_login_blocks_open_redirect(self):
        response = self.client.post(reverse("login"), {
            "username": "alice", "password": "s3cure-Passw0rd", "next": "https://evil.example.com/",
        })
        self.assertEqual(response["Location"], reverse("dashboard"))

    def test_login_follows_safe_next(self):
        response = self.client.post(reverse("login"), {
            "username": "alice", "password": "s3cure-Passw0rd", "next": "/chat/",
        })
        self.assertEqual(response["Location"], "/chat/")

    @override_settings(RATE_LIMITS={"login": "3/300"})
    def test_login_rate_limited(self):
        for _ in range(3):
            self.client.post(reverse("login"), {"username": "alice", "password": "wrong"})
        response = self.client.post(reverse("login"), {"username": "alice", "password": "s3cure-Passw0rd"})
        self.assertEqual(response.status_code, 429)

    def test_signup_rejects_duplicate_email(self):
        response = self.client.post(reverse("signup"), {
            "username": "carol", "email": "ALICE@example.com",
            "password1": "An0ther-Str0ng-pw", "password2": "An0ther-Str0ng-pw",
        })
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="carol").exists())

    def test_profile_rejects_invalid_email(self):
        self.client.force_login(self.alice)
        response = self.client.post(reverse("user_profile"), {"secondary_email": "not-an-email"})
        self.assertEqual(response.status_code, 400)
