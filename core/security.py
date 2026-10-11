"""Request hardening helpers: rate limiting, client IP resolution, upload validation."""

import hashlib
import os
import re
import time
import unicodedata

from django.conf import settings
from django.core.cache import cache

from .processor import SUPPORTED_EXTENSIONS, TEXT_EXTENSIONS, file_extension


# ── Client identity ──────────────────────────────────────────────────────────

def client_ip(request):
    """
    Resolve the client IP. With TRUSTED_PROXY_COUNT=n (n>0), the n-th entry from
    the right of X-Forwarded-For is used — the address added by your outermost
    trusted proxy — so clients can't spoof it by sending their own header.
    """
    proxies = getattr(settings, "TRUSTED_PROXY_COUNT", 0)
    if proxies:
        forwarded = [p.strip() for p in request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if p.strip()]
        if len(forwarded) >= proxies:
            return forwarded[-proxies]
    return request.META.get("REMOTE_ADDR", "unknown")


# ── Rate limiting ────────────────────────────────────────────────────────────

def _parse_rate(rate):
    count, _, seconds = rate.partition("/")
    return int(count), int(seconds or 60)


def rate_limited(scope, identity):
    """
    Fixed-window counter in the cache. Returns True when the caller has exceeded
    the limit configured in settings.RATE_LIMITS[scope]. O(1) per request.
    """
    rate = settings.RATE_LIMITS.get(scope)
    if not rate:
        return False
    limit, window = _parse_rate(rate)
    bucket = int(time.time() // window)
    key = "rl:%s:%s:%d" % (scope, hashlib.sha256(str(identity).encode()).hexdigest()[:32], bucket)
    if cache.add(key, 1, timeout=window):
        return False
    try:
        return cache.incr(key) > limit
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, timeout=window)
        return False


def user_or_ip(request):
    if request.user.is_authenticated:
        return f"u{request.user.pk}"
    return f"ip{client_ip(request)}"


# ── Upload validation ────────────────────────────────────────────────────────

class UploadRejected(Exception):
    pass


_UNSAFE_TITLE_CHARS = re.compile(r"[\x00-\x1f\x7f<>\"\\]")


def clean_title(filename):
    """A display-safe title derived from the client-supplied filename."""
    name = os.path.basename(filename.replace("\\", "/"))
    name = unicodedata.normalize("NFKC", name)
    name = _UNSAFE_TITLE_CHARS.sub("", name).strip() or "document"
    return name[:255]


def validate_upload(uploaded):
    """
    Check size, extension and file signature; return the SHA-256 of the bytes.
    Reads the upload once, in chunks, so memory stays bounded.
    """
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if uploaded.size == 0:
        raise UploadRejected("The file is empty.")
    if uploaded.size > max_bytes:
        raise UploadRejected(f"Files must be {settings.MAX_UPLOAD_SIZE_MB} MB or smaller.")

    ext = file_extension(uploaded.name)
    if ext not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(sorted(SUPPORTED_EXTENSIONS))
        raise UploadRejected(f"Unsupported file type. Allowed: {allowed}.")

    digest = hashlib.sha256()
    head = b""
    for block in uploaded.chunks():
        if len(head) < 8192:
            head += block[: 8192 - len(head)]
        digest.update(block)
    uploaded.seek(0)

    if ext == ".pdf" and b"%PDF-" not in head[:1024]:
        raise UploadRejected("This file is not a valid PDF.")
    if ext in TEXT_EXTENSIONS and b"\x00" in head:
        raise UploadRejected("Text files must contain plain text.")
    return digest.hexdigest()
