"""Provider-neutral LLM contracts with safe logging and output validation.

The Anthropic-compatible online adapter is implemented separately under
``app.llm.adapters``. This contract module contains no provider SDK imports,
network client, filesystem access, or code that interprets model/document
content as executable instructions. Mock remains the default provider.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import math
import re
from typing import Any, Protocol

from app.domain.enums import FindingCategory
from app.llm.limits import MAX_LLM_EVIDENCE_IDS, MAX_LLM_FINDINGS
from app.security.finding_text import validate_finding_text

_REDACTED = "[REDACTED]"


class LLMProviderError(RuntimeError):
    """An online provider transport failed without exposing request content."""

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        reason_code: str = "provider_error",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.http_status = http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
        self.reason_code = (
            reason_code
            if reason_code in {"timeout", "transport_error", "http_error", "provider_error"}
            else "provider_error"
        )
        self.retryable = retryable is True


class LLMConfigurationError(RuntimeError):
    """The selected provider cannot run because safe configuration is incomplete."""


VALIDATION_REASON_MESSAGES = {
    "no_text": "模型响应未包含可校验文本",
    "envelope_missing_content": "模型响应缺少内容字段",
    "unclosed_think": "模型推理块输出不完整",
    "no_complete_array": "未找到完整JSON数组",
    "multiple_arrays": "模型返回了多个JSON数组",
    "explanation_too_long": "JSON前后说明文字超过限制",
    "invalid_json": "JSON数组内容格式无效",
    "root_not_array": "模型输出根结构不是JSON数组",
    "truncated_json": "JSON数组输出不完整",
    "too_many_findings": "候选问题超过8条",
    "missing_field": "候选问题缺少必填字段或字段格式无效",
    "invalid_category": "候选问题category不在允许范围",
    "invalid_severity": "候选问题severity不在允许范围",
    "invalid_evidence": "证据引用缺失或不在本次送审范围",
}
VALIDATION_REASON_CODES = frozenset(VALIDATION_REASON_MESSAGES)
VALIDATION_CATEGORIES = frozenset({"output_format", "evidence_reference"})
SAFE_STOP_REASONS = frozenset(
    {"end_turn", "max_tokens", "stop_sequence", "tool_use", "pause_turn", "refusal", "unknown"}
)


def _optional_count(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"invalid {name}")
    return value


def safe_stop_reason(value: Any) -> str | None:
    if value is None:
        return None
    return value if isinstance(value, str) and value in SAFE_STOP_REASONS else "unknown"


class LLMValidationError(ValueError):
    """A provider returned content that failed safe structural validation."""

    def __init__(
        self,
        reason_code: str,
        *,
        validation_category: str = "output_format",
        candidate_count: int | None = None,
        valid_count: int | None = None,
        rejected_count: int | None = None,
        http_status: int | None = None,
        response_character_count: int | None = None,
        stop_reason: str | None = None,
        content_block_count: int | None = None,
    ) -> None:
        if validation_category not in VALIDATION_CATEGORIES:
            raise ValueError("invalid LLM validation category")
        if reason_code not in VALIDATION_REASON_CODES:
            raise ValueError("invalid LLM validation reason code")
        super().__init__(VALIDATION_REASON_MESSAGES[reason_code])
        self.validation_category = validation_category
        self.reason_code = reason_code
        self.candidate_count = _optional_count(candidate_count, "candidate_count")
        self.valid_count = _optional_count(valid_count, "valid_count")
        self.rejected_count = _optional_count(rejected_count, "rejected_count")
        self.http_status = http_status if isinstance(http_status, int) and 100 <= http_status <= 599 else None
        self.response_character_count = _optional_count(response_character_count, "response_character_count")
        self.stop_reason = safe_stop_reason(stop_reason)
        self.content_block_count = _optional_count(content_block_count, "content_block_count")

    @property
    def category(self) -> str:
        """Compatibility alias for callers introduced before reason codes."""
        return self.validation_category


_SAFE_FLOAT_OPTION_KEYS = frozenset(
    {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
)
_SAFE_INT_OPTION_KEYS = frozenset({"max_tokens", "timeout", "seed"})
_SAFE_BOOL_OPTION_KEYS = frozenset({"stream"})
_SAFE_SCALAR_OPTION_KEYS = (
    _SAFE_FLOAT_OPTION_KEYS | _SAFE_INT_OPTION_KEYS | _SAFE_BOOL_OPTION_KEYS
)
_SAFE_EVIDENCE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_SAFE_OPTION_LOG_KEYS = frozenset(
    {"temperature", "max_tokens", "top_p", "timeout", "stream", "seed", "presence_penalty", "frequency_penalty"}
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
    http_status: int | None = None
    response_character_count: int | None = None
    stop_reason: str | None = None
    content_block_count: int | None = None


@dataclass(frozen=True)
class LLMConnectionResult:
    provider: str
    model: str
    http_status: int


class LLMProvider(Protocol):
    """Minimal provider abstraction; implementations must return validated data."""

    provider_name: str
    model_name: str

    def review(self, request: LLMRequest) -> LLMResponse: ...

    def test_connection(self) -> LLMConnectionResult: ...


def validate_findings(
    findings: Iterable[Mapping[str, Any]], allowed_evidence_span_ids: Iterable[str]
) -> list[dict[str, Any]]:
    """Validate model findings against the evidence supplied in the request.

    Findings must be structured, reference only requested evidence, and use the
    application's severity vocabulary.  Returned dictionaries/lists are copies
    so callers cannot mutate the provider's internal result through aliases.
    """
    allowed_ids = set(allowed_evidence_span_ids)
    try:
        finding_items = list(findings)
    except TypeError:
        raise LLMValidationError("missing_field") from None
    if len(finding_items) > MAX_LLM_FINDINGS:
        raise LLMValidationError(
            "too_many_findings",
            candidate_count=len(finding_items),
            valid_count=0,
            rejected_count=len(finding_items),
        )
    validated: list[dict[str, Any]] = []
    for finding in finding_items:
        if not isinstance(finding, Mapping):
            raise LLMValidationError(
                "missing_field", candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        missing = _REQUIRED_FINDING_FIELDS.difference(finding)
        if missing:
            evidence_error = "evidence_span_ids" in missing
            raise LLMValidationError(
                "invalid_evidence" if evidence_error else "missing_field",
                validation_category="evidence_reference" if evidence_error else "output_format",
                candidate_count=len(finding_items),
                valid_count=0,
                rejected_count=len(finding_items),
            )
        if not all(isinstance(finding[name], str) for name in _REQUIRED_FINDING_FIELDS - {"evidence_span_ids"}):
            raise LLMValidationError(
                "missing_field", candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        for field_name in ("title", "description", "suggestion"):
            try:
                validate_finding_text(finding[field_name], field_name)
            except (TypeError, ValueError) as exc:
                raise LLMValidationError(
                    "missing_field", candidate_count=len(finding_items),
                    valid_count=0, rejected_count=len(finding_items),
                ) from None
        if finding["severity"] not in _ALLOWED_SEVERITIES:
            raise LLMValidationError(
                "invalid_severity", candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        try:
            category = FindingCategory(finding["category"])
        except (TypeError, ValueError) as exc:
            raise LLMValidationError(
                "invalid_category", candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            ) from None

        evidence_ids = finding["evidence_span_ids"]
        if not isinstance(evidence_ids, list) or not all(isinstance(value, str) for value in evidence_ids):
            raise LLMValidationError(
                "invalid_evidence",
                validation_category="evidence_reference",
                candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        if not evidence_ids:
            raise LLMValidationError(
                "invalid_evidence",
                validation_category="evidence_reference",
                candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        if len(evidence_ids) > MAX_LLM_EVIDENCE_IDS:
            raise LLMValidationError(
                "invalid_evidence",
                validation_category="evidence_reference",
                candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )
        unknown_ids = set(evidence_ids).difference(allowed_ids)
        if unknown_ids:
            raise LLMValidationError(
                "invalid_evidence",
                validation_category="evidence_reference",
                candidate_count=len(finding_items),
                valid_count=0, rejected_count=len(finding_items),
            )

        safe_finding = dict(finding)
        safe_finding["category"] = category.value
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
    if normalized_key in _SAFE_FLOAT_OPTION_KEYS:
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else _REDACTED
    if normalized_key in _SAFE_INT_OPTION_KEYS:
        return value if isinstance(value, int) and not isinstance(value, bool) else _REDACTED
    if normalized_key in _SAFE_BOOL_OPTION_KEYS:
        return value if isinstance(value, bool) else _REDACTED
    return _REDACTED


def _safe_evidence_span_ids(span_ids: Iterable[Any]) -> list[str]:
    """Keep only opaque, bounded IDs; never emit arbitrary caller strings."""
    return [
        value if isinstance(value, str) and _SAFE_EVIDENCE_ID_PATTERN.fullmatch(value) else _REDACTED
        for value in span_ids
    ]


def _safe_model_identifier(model: Any) -> str:
    if isinstance(model, str) and _SAFE_EVIDENCE_ID_PATTERN.fullmatch(model):
        return model
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
        "model": _safe_model_identifier(request.model),
        "system_prompt": _REDACTED,
        "user_content": _REDACTED,
        "evidence_span_ids": _safe_evidence_span_ids(request.evidence_span_ids),
    }
    unknown_options = False
    for key, value in (provider_options or {}).items():
        normalized_key = key.casefold().replace("-", "_") if isinstance(key, str) else ""
        if normalized_key in _SAFE_OPTION_LOG_KEYS:
            redacted[normalized_key] = _redact_option_value(normalized_key, value)
        else:
            unknown_options = True
    if unknown_options:
        redacted["redacted_options"] = _REDACTED
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
