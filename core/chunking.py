"""Text normalisation and recursive character chunking for the ingestion pipeline."""

import re

_SEPARATORS = ("\n\n", "\n", ". ", " ", "")
_INLINE_WS = re.compile(r"[ \t\f\v ]+")
_BLANK_LINES = re.compile(r"\n\s*\n+")
_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")


def normalize_text(text):
    """Clean up PDF-extracted text: rejoin hyphenated line breaks, collapse whitespace."""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    text = _INLINE_WS.sub(" ", text)
    text = _BLANK_LINES.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


def _split(text, chunk_size, separators):
    """Split text into pieces no longer than chunk_size, preferring coarse separators."""
    if len(text) <= chunk_size:
        return [text]

    sep, rest = separators[0], separators[1:]
    if sep == "":
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)]

    parts = text.split(sep)
    pieces, buf, buf_len = [], [], 0
    last = len(parts) - 1
    for i, part in enumerate(parts):
        piece = part if i == last else part + sep
        if not piece:
            continue
        if buf_len + len(piece) <= chunk_size:
            buf.append(piece)
            buf_len += len(piece)
            continue
        if buf:
            pieces.append("".join(buf))
            buf, buf_len = [], 0
        if len(piece) > chunk_size:
            pieces.extend(_split(piece, chunk_size, rest))
        else:
            buf, buf_len = [piece], len(piece)
    if buf:
        pieces.append("".join(buf))
    return pieces


def split_text(text, chunk_size=1000, overlap=200):
    """
    Recursive character text splitting (paragraph → line → sentence → word → char).

    Each chunk after the first is prefixed with up to `overlap` characters from the
    end of the previous chunk (trimmed to a word boundary) so that context spanning
    a boundary is not lost. Runs in O(n) over the input length.
    """
    if overlap >= chunk_size:
        raise ValueError("overlap must be smaller than chunk_size")

    text = text.strip()
    if not text:
        return []

    raw = [p.strip() for p in _split(text, chunk_size, _SEPARATORS)]
    raw = [p for p in raw if p]
    if overlap <= 0 or len(raw) < 2:
        return raw

    chunks = [raw[0]]
    for prev, cur in zip(raw, raw[1:]):
        tail = prev[-overlap:]
        space = tail.find(" ")
        if 0 <= space < len(tail) - 1 and len(prev) > overlap:
            tail = tail[space + 1:]
        chunks.append(f"{tail} {cur}")
    return chunks
