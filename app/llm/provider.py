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
_SENSITIVE_KEY_MARKERS = frozenset(
    {"api_key", "apikey", "authorization", "credential", "password", "secret", "token"}
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


def redact_request_for_log(
    request: LLMRequest, provider_options: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return non-sensitive request metadata suitable for application logs.

    Prompts and document body are always redacted.  Option keys containing
    credentials, keys, or tokens are also redacted before any logging boundary.
    """
    redacted: dict[str, Any] = {
        "model": request.model,
        "system_prompt": _REDACTED,
        "user_content": _REDACTED,
        "evidence_span_ids": list(request.evidence_span_ids),
    }
    for key, value in (provider_options or {}).items():
        normalized_key = key.casefold().replace("-", "_")
        redacted[key] = (
            _REDACTED
            if any(marker in normalized_key for marker in _SENSITIVE_KEY_MARKERS)
            else value
        )
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
