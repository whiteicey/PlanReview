"""Shared length and sensitive-content policy for persisted finding prose."""

from __future__ import annotations

import re
from typing import Literal

FindingTextField = Literal["title", "description", "suggestion"]

MAX_FINDING_TITLE_CHARACTERS = 200
MAX_FINDING_DESCRIPTION_CHARACTERS = 4_000
MAX_FINDING_SUGGESTION_CHARACTERS = 4_000

_LIMITS: dict[FindingTextField, int] = {
    "title": MAX_FINDING_TITLE_CHARACTERS,
    "description": MAX_FINDING_DESCRIPTION_CHARACTERS,
    "suggestion": MAX_FINDING_SUGGESTION_CHARACTERS,
}

_KNOWN_CREDENTIAL = re.compile(
    r"(?i)\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16})\b"
)
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[:=]\s*"
    r"(?:bearer\s+|basic\s+)?[A-Za-z0-9+/_.=-]{12,}"
)
_AUTHORIZATION_HEADER = re.compile(
    r"(?im)^\s*authorization\s*:\s*(?:bearer|basic)\s+\S+"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")
_LABELED_BODY_DUMP = re.compile(
    r"(?is)^\s*(?:request|response)(?:\s+(?:body|payload)|_(?:body|payload))?\s*:\s*"
    r"(?:\{|\[|(?:GET|POST|PUT|PATCH|DELETE)\s+|HTTP/)"
)
_HTTP_REQUEST_LINE = re.compile(
    r"(?im)^\s*(?:GET|POST|PUT|PATCH|DELETE)\s+\S+\s+HTTP/\d"
)


def validate_finding_text(value: str, field: FindingTextField) -> str:
    """Return safe finding prose or fail closed on bounded, explicit hazards."""
    if not isinstance(value, str):
        raise TypeError(f"finding {field} must be a string")
    limit = _LIMITS[field]
    if len(value) > limit:
        raise ValueError(
            f"finding {field} exceeds bounded limit ({limit} characters)"
        )
    if any(
        pattern.search(value)
        for pattern in (
            _KNOWN_CREDENTIAL,
            _CREDENTIAL_ASSIGNMENT,
            _AUTHORIZATION_HEADER,
            _PRIVATE_KEY,
            _JWT,
            _LABELED_BODY_DUMP,
            _HTTP_REQUEST_LINE,
        )
    ):
        raise ValueError("finding text contains sensitive content")
    return value
