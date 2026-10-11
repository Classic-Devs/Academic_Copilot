"""Verify Gemini and the vector store are reachable and correctly configured."""

from django.conf import settings
from django.core.management.base import BaseCommand

from core.ai_service import SYSTEM_INSTRUCTION, get_ai_service


class Command(BaseCommand):
    help = "Check embedding, generation and vector-store connectivity."

    def add_arguments(self, parser):
        parser.add_argument("--list-models", action="store_true", help="List models available to the API key.")

    def handle(self, *args, **opts):
        service = get_ai_service()
        ok = True

        if opts["list_models"]:
            for model in service.client.models.list():
                actions = ", ".join(getattr(model, "supported_actions", None) or [])
                self.stdout.write(f"  {model.name}  [{actions}]")

        try:
            vec = service.embeddings.embed_query("What is hybrid inheritance?")
            self.stdout.write(self.style.SUCCESS(
                f"Embedding OK: {settings.GEMINI_EMBEDDING_MODEL}, {len(vec)} dims"))
        except Exception as exc:
            ok = False
            self.stderr.write(f"Embedding FAILED: {exc}")

        try:
            text = service.chat.generate("Reply with the single word: ready", SYSTEM_INSTRUCTION)
            self.stdout.write(self.style.SUCCESS(f"Generation OK: {settings.GEMINI_CHAT_MODEL} → {text.strip()[:60]!r}"))
        except Exception as exc:
            ok = False
            self.stderr.write(f"Generation FAILED: {exc}")

        try:
            self.stdout.write(self.style.SUCCESS(
                f"Vector store OK: collection '{settings.CHROMA_COLLECTION}' holds {service.vectors.count()} vectors"))
        except Exception as exc:
            ok = False
            self.stderr.write(f"Vector store FAILED: {exc}")

        if not ok:
            raise SystemExit(1)
