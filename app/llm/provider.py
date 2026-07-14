"""Provider-neutral LLM contracts with safe logging and output validation.

The concrete online provider adapters are intentionally deferred.  This module
contains no provider SDK imports, network client, filesystem access, or code
that interprets model/document content as executable instructions.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

_REDACTED = "[REDACTED]"
_SAFE_SCALAR_OPTION_KEYS = frozenset(
    {
        "temperature",
        "max_tokens",
        "top_p",
        "timeout",
        "stream",
        "seed",
        "presence_penalty",
        "frequency_penalty",
    }
)
_SENSITIVE_KEY_NAMES = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "credential",
        "password",
        "secret",
        "token",
    }
)
_SENSITIVE_KEY_SUFFIXES = ("_key", "_secret", "_token")
_BODY_BEARING_KEYS = frozenset(
    {
        "body",
        "content",
        "document",
        "documents",
        "input",
        "inputs",
        "message",
        "messages",
        "payload",
        "prompt",
        "prompts",
    }
)
_REQUIRED_FINDING_FIELDS = frozenset(
    {"category", "severity", "title", "description", "suggestion", "evidence_span_ids"}
)
_ALLOWED_SEVERITIES = frozenset({"high", "medium", "low"})


@dataclass(frozen=True)
class LLMRequest:
    """Data-only request supplied to an LLM provider."""

    model: str
    system_prompt: str
    user_content: str
    evidence_span_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMResponse:
    """Provider-neutral structured response with traceable evidence."""

    provider: str
    model: str
    findings: list[dict[str, Any]]
    request_id: str | None = None


class LLMProvider(Protocol):
    """Minimal provider abstraction; implementations must return validated data."""

    def review(self, request: LLMRequest) -> LLMResponse: ...


def validate_findings(
    findings: Iterable[Mapping[str, Any]], allowed_evidence_span_ids: Iterable[str]
) -> list[dict[str, Any]]:
    """Validate model findings against the evidence supplied in the request.

    Findings must be structured, reference only requested evidence, and use the
    application's severity vocabulary.  Returned dictionaries/lists are copies
    so callers cannot mutate the provider's internal result through aliases.
    """
    allowed_ids = set(allowed_evidence_span_ids)
    validated: list[dict[str, Any]] = []
    for finding in findings:
        missing = _REQUIRED_FINDING_FIELDS.difference(finding)
        if missing:
            names = ", ".join(sorted(missing))
            raise ValueError(f"finding missing required field(s): {names}")
        if not all(isinstance(finding[name], str) for name in _REQUIRED_FINDING_FIELDS - {"evidence_span_ids"}):
            raise ValueError("finding text fields must be strings")
        if finding["severity"] not in _ALLOWED_SEVERITIES:
            raise ValueError(f"invalid severity: {finding['severity']!r}")

        evidence_ids = finding["evidence_span_ids"]
        if not isinstance(evidence_ids, list) or not all(isinstance(value, str) for value in evidence_ids):
            raise ValueError("evidence_span_ids must be a list of strings")
        unknown_ids = set(evidence_ids).difference(allowed_ids)
        if unknown_ids:
            names = ", ".join(sorted(unknown_ids))
            raise ValueError(f"finding references unknown evidence span(s): {names}")

        safe_finding = dict(finding)
        safe_finding["evidence_span_ids"] = list(evidence_ids)
        validated.append(safe_finding)
    return validated


def _redact_option_value(key: str, value: Any) -> Any:
    """Keep only explicitly safe scalar provider metadata for logging.

    Unknown values are redacted by default.  This is intentionally stricter
    than recursively copying arbitrary mappings: provider payload schemas can
    change, and document content must never cross the logging boundary.
    """
    normalized_key = key.casefold().replace("-", "_")
    if (
        normalized_key in _SENSITIVE_KEY_NAMES
        or normalized_key.endswith(_SENSITIVE_KEY_SUFFIXES)
        or normalized_key == "private_key"
    ):
        return _REDACTED
    if normalized_key in _BODY_BEARING_KEYS or any(
        marker in normalized_key for marker in _BODY_BEARING_KEYS
    ):
        return _REDACTED
    if normalized_key not in _SAFE_SCALAR_OPTION_KEYS:
        return _REDACTED
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return _REDACTED


def redact_request_for_log(
    request: LLMRequest, provider_options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return non-sensitive request metadata suitable for application logs.

    Prompts and document bodies are always redacted. Provider options use a
    strict allowlist of safe scalar tuning metadata; nested headers,
    credentials, payloads, messages, and unknown values are never copied.
    """
    redacted: dict[str, Any] = {
        "model": request.model,
        "system_prompt": _REDACTED,
        "user_content": _REDACTED,
        "evidence_span_ids": list(request.evidence_span_ids),
    }
    for key, value in (provider_options or {}).items():
        redacted[key] = _redact_option_value(key, value)
    return redacted


class _DeferredOnlineProvider:
    """Safety boundary for online provider integrations not enabled in this task."""

    def review(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError(
            "deferred until real provider is explicitly enabled; no request content is processed"
        )


class AnthropicProvider(_DeferredOnlineProvider):
    """Deferred Anthropic adapter: no SDK, network, or local-path handling."""


class OpenAIProvider(_DeferredOnlineProvider):
    """Deferred OpenAI adapter: no SDK, network, or local-path handling."""
