"""Defensive redaction helpers for structured log payloads."""

from __future__ import annotations

from collections.abc import Mapping


_REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = ("key", "token", "secret", "password", "authorization")
_FULL_BODY_KEY_PARTS = ("body", "content", "payload", "request", "response")


def _key_requires_redaction(key: object) -> bool:
    """Identify keys whose complete value must not cross the log boundary."""
    if not isinstance(key, str):
        return False
    normalized = key.casefold()
    return any(
        part in normalized
        for part in _SENSITIVE_KEY_PARTS + _FULL_BODY_KEY_PARTS
    )


def redact_log_payload(payload: object) -> object:
    """Return a deep redacted copy suitable for structured logging.

    Values associated with credential-like keys and full request/response body
    keys are replaced wholesale. Mappings and builtin sequences are traversed
    recursively, retaining only non-sensitive metadata. Arbitrary object
    instances are returned as-is because serializing them here could invoke
    user-defined code or materialize hidden data.
    """
    if isinstance(payload, Mapping):
        return {
            key: _REDACTED
            if _key_requires_redaction(key)
            else redact_log_payload(value)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [redact_log_payload(value) for value in payload]
    if isinstance(payload, tuple):
        return tuple(redact_log_payload(value) for value in payload)
    if isinstance(payload, set):
        return {redact_log_payload(value) for value in payload}
    if isinstance(payload, frozenset):
        return frozenset(redact_log_payload(value) for value in payload)
    return payload
