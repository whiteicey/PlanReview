from __future__ import annotations

import hashlib


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hexadecimal SHA-256 digest for binary data."""
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    """Return the lowercase hexadecimal SHA-256 digest for UTF-8 text."""
    return sha256_bytes(text.encode("utf-8"))
