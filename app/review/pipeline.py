"""End-to-end, fail-closed review pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import logging
import os
import re
import time
from typing import Callable

from pydantic import ValidationError

from app.domain.enums import BlockType, LLMStatus, Origin, PipelineStage, Severity
from app.domain.ids import new_review_run_id
from app.domain.schemas import Finding, ParameterFact, RuleDefinition, RuleResult, SourceSpan, StageRecord
from app.extraction.normalization import (
    coalesce_redundant_unscoped_facts,
    normalize_facts_units,
)
from app.extraction.parameters import extract_parameter_facts
from app.extraction.source_version import parse_source_version
from app.extraction.terminology import TerminologyMap, normalize_facts
from app.llm.limits import (
    MAX_LLM_EVIDENCE_IDS,
    MAX_LLM_SINGLE_SPAN_CHARACTERS,
    MAX_LLM_SPANS,
    MAX_LLM_TOTAL_CHARACTERS,
)
from app.llm.provider import (
    LLMConfigurationError,
    LLMProvider,
    LLMProviderError,
    LLMRequest,
    LLMResponse,
    LLMValidationError,
    validate_findings,
)
from app.parsers.docx_parser import ParsedDocument
from app.pipeline import StageRunner
from app.review.reconcile import merge_findings, rule_results_to_findings
from app.review.evidence_packets import (
    EVIDENCE_SELECTOR_VERSION,
    build_evidence_plan,
    expand_packet_evidence,
)
from app.review.finding_guards import deduplicate_findings, filter_unsupported_ai_findings
from app.review.ledgers import LEDGER_SCHEMA_VERSION, configured_ledger, empty_ledger
from app.review.progress import ProgressCallback
from app.rules.engine import RuleEngine

_SAFE_SPAN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
PROMPT_VERSION = "structured-review-json-v1"
MAX_AI_STAGE_SECONDS = 15 * 60

CheckpointCallback = Callable[["ReviewRun", list[Finding]], None]


def format_ai_batch_progress(batch_index: int, batch_count: int) -> str:
    return f"已完成第{batch_index}批AI审查，共{batch_count}批"


@dataclass(frozen=True)
class LLMEvidenceSelection:
    original_count: int
    selected_ids: list[str]
    user_content: str
    selected_character_count: int
    partial: bool


def select_llm_evidence(
    spans: list[SourceSpan],
    rule_results: list[RuleResult],
    facts: list[ParameterFact],
) -> LLMEvidenceSelection:
    """Select a bounded, deterministic subset without limiting local review."""
    available: list[SourceSpan] = []
    seen_available: set[str] = set()
    for span in spans:
        text = span.text if isinstance(span.text, str) else ""
        if (
            not isinstance(span.span_id, str)
            or not _SAFE_SPAN_ID.fullmatch(span.span_id)
            or span.span_id in seen_available
            or not text
        ):
            continue
        seen_available.add(span.span_id)
        available.append(span)

    span_by_id = {span.span_id: span for span in available}
    ordered_ids: list[str] = []
    selected_for_priority: set[str] = set()

    def add_ids(values, limit: int | None = None) -> None:
        added = 0
        for span_id in values:
            if limit is not None and added >= limit:
                break
            if span_id in span_by_id and span_id not in selected_for_priority:
                selected_for_priority.add(span_id)
                ordered_ids.append(span_id)
                added += 1

    fail_ids = (
        span_id
        for result in rule_results
        if result.status.value == "FAIL"
        for span_id in result.evidence_span_ids
    )
    unknown_ids = (
        span_id
        for result in rule_results
        if result.status.value == "UNKNOWN"
        for span_id in result.evidence_span_ids
    )
    add_ids(fail_ids, 20)
    add_ids(unknown_ids, 8)
    fact_ids = (
        span_id
        for fact in facts
        for span_id in [fact.source_span_id, *fact.merged_span_ids]
    )
    add_ids(fact_ids, 8)

    anchor_ids = list(ordered_ids)
    if anchor_ids:
        positions = {span.span_id: index for index, span in enumerate(available)}
        context_ids: list[str] = []
        for anchor_id in anchor_ids:
            anchor_index = positions[anchor_id]
            anchor = available[anchor_index]
            for neighbor_index in (anchor_index - 1, anchor_index + 1):
                if 0 <= neighbor_index < len(available):
                    neighbor = available[neighbor_index]
                    if neighbor.document_id == anchor.document_id:
                        context_ids.append(neighbor.span_id)
        add_ids(context_ids)
        add_ids(
            span.span_id for span in available if span.block_type is BlockType.HEADING
        )
    else:
        add_ids(span.span_id for span in available)

    blocks: list[str] = []
    sent_ids: list[str] = []
    content_truncated = False
    for span_id in ordered_ids:
        if len(sent_ids) >= min(MAX_LLM_SPANS, MAX_LLM_EVIDENCE_IDS):
            break
        span = span_by_id[span_id]
        original_text = span.text
        text = original_text[:MAX_LLM_SINGLE_SPAN_CHARACTERS]
        if text != original_text:
            content_truncated = True
        prefix = f"[{span_id}]\n"
        separator_length = 2 if blocks else 0
        current_length = sum(len(block) for block in blocks) + 2 * max(0, len(blocks) - 1)
        available_text_length = MAX_LLM_TOTAL_CHARACTERS - current_length - separator_length - len(prefix)
        if available_text_length <= 0:
            break
        if len(text) > available_text_length:
            text = text[:available_text_length]
            content_truncated = True
        if not text:
            break
        blocks.append(prefix + text)
        sent_ids.append(span_id)

    user_content = "\n\n".join(blocks)
    return LLMEvidenceSelection(
        original_count=len(available),
        selected_ids=sent_ids,
        user_content=user_content,
        selected_character_count=len(user_content),
        partial=content_truncated or len(sent_ids) < len(available),
    )


def format_span_location(span: SourceSpan) -> str:
    """Human-readable source location for a span, for identified reports.

    Examples: "附件A关键参数表 第9行第2列"、"正文 第7段"、"标题：产能与生产预测".
    Uses 1-based row/column/paragraph numbers so it reads naturally.
    """
    section = " / ".join(part for part in span.section_path if part)
    if span.block_type is BlockType.TABLE_CELL and span.row_index is not None and span.column_index is not None:
        where = f"表格 第{span.row_index + 1}行第{span.column_index + 1}列"
    elif span.block_type is BlockType.HEADING:
        where = "标题"
    elif span.paragraph_index is not None:
        where = f"第{span.paragraph_index + 1}段"
    else:
        where = "正文"
    return f"{section} {where}".strip() if section else where


@dataclass
class ReviewRun:
    """Retained review artefacts, including facts required for exact exports."""

    case_id: str
    run_id: str = field(default_factory=new_review_run_id)
    created_at: datetime | None = None
    facts: list[ParameterFact] = field(default_factory=list)
    rule_results: list[RuleResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    stage_records: list[StageRecord] = field(default_factory=list)
    final_status: str = "PENDING"
    # Retain only evidence hashes for anonymous exports; source span text is not kept.
    evidence_text_hashes: dict[str, str] = field(default_factory=dict)
    # Human-readable location per evidence span (section / paragraph / table cell)
    # for identified-report exports; NOT included in the anonymous package.
    evidence_locations: dict[str, str] = field(default_factory=dict)
    # Set when an online LLM review could not be completed (fail-closed): the
    # rule results are unaffected, but the client is told the AI pass was skipped.
    llm_review_error: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_status: LLMStatus = LLMStatus.NOT_RUN
    llm_finding_count: int = 0
    llm_error_summary: str | None = None
    validation_reason_code: str | None = None
    candidate_count: int | None = None
    valid_count: int | None = None
    rejected_count: int | None = None
    available_span_count: int | None = None
    selected_span_count: int | None = None
    selected_character_count: int | None = None
    coverage_ratio: float | None = None
    git_sha: str | None = None
    prompt_version: str | None = PROMPT_VERSION
    evidence_selector_version: str | None = EVIDENCE_SELECTOR_VERSION
    max_tokens: int | None = None
    timeout: float | None = None
    temperature: float | None = None
    batch_count: int = 0
    batch_metrics: list[dict] = field(default_factory=list)
    premerge_finding_count: int = 0
    postmerge_finding_count: int = 0
    deduplicated_finding_count: int = 0
    stop_reason: str | None = None
    ai_guard_rejections: list[dict] = field(default_factory=list)
    deduplication_records: list[dict] = field(default_factory=list)
    packet_lifecycle_ledger: dict = field(default_factory=lambda: empty_ledger("packet_lifecycle"))
    ai_candidate_lifecycle_ledger: dict = field(default_factory=lambda: empty_ledger("ai_candidate_lifecycle"))
    rule_metrics: dict = field(default_factory=dict)


class ReviewPipeline:
    """Orchestrate deterministic local review stages without skipping failures."""

    def __init__(self, terminology: TerminologyMap | None = None) -> None:
        self.terminology = terminology

    def run(
        self,
        case_id: str,
        documents: list[ParsedDocument],
        rules: list[RuleDefinition],
        provider: LLMProvider,
        *,
        run_id: str | None = None,
        progress: ProgressCallback | None = None,
        checkpoint: CheckpointCallback | None = None,
    ) -> ReviewRun:
        state = ReviewRun(case_id=case_id) if run_id is None else ReviewRun(case_id=case_id, run_id=run_id)
        provider_name = getattr(provider, "provider_name", None)
        model_name = getattr(provider, "model_name", None)
        state.llm_provider = provider_name if isinstance(provider_name, str) else None
        state.llm_model = model_name if isinstance(model_name, str) else None
        git_sha = os.environ.get("REVIEW_GIT_SHA")
        state.git_sha = git_sha if git_sha and re.fullmatch(r"[0-9a-fA-F]{7,64}", git_sha) else None
        state.max_tokens = getattr(provider, "max_tokens", None)
        state.timeout = getattr(provider, "timeout", None)
        state.temperature = getattr(provider, "temperature", None)
        spans: list[SourceSpan] = []
        raw_facts: list[ParameterFact] = []
        llm_findings: list[Finding] = []
        packet_ledger = configured_ledger("packet_lifecycle")
        candidate_ledger = configured_ledger("ai_candidate_lifecycle")
        state.packet_lifecycle_ledger = packet_ledger.to_dict()
        state.ai_candidate_lifecycle_ledger = candidate_ledger.to_dict()

        def emit(stage, event_type, status, message, details=None) -> None:
            if progress is not None:
                try:
                    progress(stage, event_type, status, message, details)
                except Exception:
                    # Progress reporting is auxiliary.  It must never discard a
                    # completed provider response or fail the review pipeline.
                    logging.warning(
                        "Progress callback failed for %s/%s",
                        stage,
                        event_type,
                        exc_info=True,
                    )

        def persist_checkpoint(findings: list[Finding]) -> None:
            if checkpoint is None:
                return
            try:
                checkpoint(state, findings)
            except Exception:
                # A transient observability checkpoint must not discard a
                # validated provider response. The final immutable save remains
                # authoritative and the warning is retained in service logs.
                logging.warning("AI batch checkpoint persistence failed", exc_info=True)

        def uploaded() -> None:
            if not isinstance(case_id, str) or not case_id:
                raise ValueError("case_id is required")
            if not all(isinstance(document, ParsedDocument) for document in documents):
                raise TypeError("documents must be parsed documents")

        def parsed() -> None:
            spans.extend(span for document in documents for span in document.spans)
            state.evidence_text_hashes = {
                span.span_id: span.text_hash for span in spans
            }
            state.evidence_locations = {
                span.span_id: format_span_location(span) for span in spans
            }

        def extracted() -> None:
            emit("PARAMETER_EXTRACTION", "STAGE_STARTED", "running", "正在提取关键参数")
            for document in documents:
                source_version = parse_source_version(document.file_name)
                facts = extract_parameter_facts(document, source_version=source_version)
                if self.terminology is not None:
                    facts = normalize_facts(facts, self.terminology)
                raw_facts.extend(facts)
            emit(
                "PARAMETER_EXTRACTION", "STAGE_COMPLETED", "completed",
                f"共提取 {len(raw_facts)} 个参数", {"parameter_count": len(raw_facts)}
            )

        def normalized() -> None:
            emit("PARAMETER_NORMALIZATION", "STAGE_STARTED", "running", "正在规范化参数名称和单位")
            state.facts = coalesce_redundant_unscoped_facts(
                normalize_facts_units(raw_facts)
            )
            emit(
                "PARAMETER_NORMALIZATION", "STAGE_COMPLETED", "completed",
                f"已形成 {len(state.facts)} 个规范化参数",
                {"normalized_fact_count": len(state.facts), "deduplicated_fact_count": max(0, len(raw_facts) - len(state.facts))},
            )

        def rule_checked() -> None:
            applicable = [rule for rule in rules if rule.enabled]
            emit("RULE_CONFIG", "STAGE_STARTED", "running", "正在加载规则配置")
            emit(
                "RULE_CONFIG", "STAGE_COMPLETED", "completed", f"已加载 {len(applicable)} 条适用规则",
                {"applicable_rule_count": len(applicable), "not_applicable_rule_count": max(0, len(rules) - len(applicable))},
            )
            emit(
                "RULE_CHECK", "STAGE_STARTED", "running",
                f"正在执行 {len(applicable)} 条确定性规则",
                {"applicable_rule_count": len(applicable), "completed_rule_count": 0},
            )
            completed_count = 0

            def observe_rule(kind, rule, results) -> None:
                nonlocal completed_count
                if kind == "started":
                    emit("RULE_CHECK", "RULE_STARTED", "running", f"正在执行 {rule.rule_id}", {"rule_id": rule.rule_id})
                    return
                completed_count += 1
                values = {item.status.value for item in results}
                result = "FAIL" if "fail" in values else "UNKNOWN" if "unknown" in values else "PASS"
                emit(
                    "RULE_CHECK", "RULE_COMPLETED",
                    "completed" if result == "PASS" else "partial",
                    f"{rule.rule_id}：{result}",
                    {"rule_id": rule.rule_id, "result": result, "completed_rule_count": completed_count},
                )

            state.rule_results = RuleEngine().evaluate(rules, state.facts, spans, observer=observe_rule)
            v12_ids = {
                "REFERENCE-001",
                "SUMMARY_DETAIL-001",
                "CROSS_SOURCE_PARAM-001",
                "UNIT_MAGNITUDE-001",
                "SCHEDULE-001",
                "EQUIPMENT_REDUNDANCY-001",
            }
            state.rule_metrics = {}
            for rule in rules:
                if rule.rule_id not in v12_ids:
                    continue
                outcomes = [item for item in state.rule_results if item.rule_id == rule.rule_id]
                state.rule_metrics[rule.rule_id] = {
                    "rule_id": rule.rule_id,
                    "enabled": bool(rule.enabled),
                    "executed_count": len(outcomes),
                    "pass_count": sum(item.status.value == "PASS" for item in outcomes),
                    "fail_count": sum(item.status.value == "FAIL" for item in outcomes),
                    "unknown_count": sum(item.status.value == "UNKNOWN" for item in outcomes),
                    "matched_tp": 0,
                    "fp": 0,
                    "baseline_new_findings": 0,
                }
            emit(
                "RULE_CHECK", "STAGE_COMPLETED", "completed", "确定性规则检查完成",
                {"applicable_rule_count": len(applicable), "completed_rule_count": completed_count},
            )

        def llm_reviewed() -> None:
            emit("AI_EVIDENCE", "STAGE_STARTED", "running", "正在准备 AI 复核证据")
            selection = select_llm_evidence(spans, state.rule_results, state.facts)
            sent_ids = selection.selected_ids
            coverage_ratio = (
                round(len(sent_ids) / selection.original_count, 4)
                if selection.original_count else 0.0
            )
            state.available_span_count = selection.original_count
            state.selected_span_count = len(sent_ids)
            state.selected_character_count = selection.selected_character_count
            state.coverage_ratio = coverage_ratio
            evidence_metrics = {
                "available_span_count": selection.original_count,
                "original_evidence_count": selection.original_count,
                "selected_span_count": len(sent_ids),
                "selected_character_count": selection.selected_character_count,
                "coverage_ratio": coverage_ratio,
                "ai_coverage_ratio": round(coverage_ratio * 100, 1),
            }
            if not selection.user_content:
                state.llm_status = LLMStatus.INPUT_LIMIT_EXCEEDED
                state.llm_error_summary = "No valid evidence spans were available for LLM review"
                state.llm_review_error = state.llm_error_summary
                emit("AI_EVIDENCE", "STAGE_COMPLETED", "failed", "没有可用于 AI 复核的证据片段", evidence_metrics)
                emit("AI_REVIEW", "STAGE_COMPLETED", "skipped", "AI 复核已跳过")
                emit("AI_VALIDATION", "STAGE_COMPLETED", "skipped", "AI 输出校验已跳过")
                return
            emit(
                "AI_EVIDENCE", "STAGE_COMPLETED", "completed",
                f"已从 {selection.original_count} 个可用证据片段中选择 {len(sent_ids)} 个重点片段",
                evidence_metrics,
            )
            emit("AI_REVIEW", "STAGE_STARTED", "running", "正在连接 AI 服务", {"provider": state.llm_provider or "unknown"})
            try:
                llm_request = LLMRequest(
                    model=state.llm_model or "unknown",
                    system_prompt=(
                        "最多返回8条最重要的问题；只返回JSON数组；不得输出说明、Markdown、代码框或<think>；"
                        "无问题时只返回[]。category仅允许completeness、consistency、aggregation、"
                        "cross_domain、capacity、version_change、terminology、evidence、traceability、"
                        "unknown_scope、other；severity仅允许high、medium、low。"
                    ),
                    user_content=selection.user_content,
                    evidence_span_ids=sent_ids,
                )
                try:
                    response = provider.review(llm_request)
                except LLMProviderError as first_error:
                    if not first_error.retryable:
                        raise
                    retry_details = {
                        "retry_attempt": 1,
                        "provider_error_code": first_error.reason_code,
                    }
                    if first_error.http_status is not None:
                        retry_details["http_status"] = first_error.http_status
                    emit(
                        "AI_REVIEW",
                        "LLM_RETRY_SCHEDULED",
                        "partial",
                        "AI 服务调用出现可重试错误，已记录首次失败并执行一次受控重试",
                        retry_details,
                    )
                    response = provider.review(llm_request)
            except LLMConfigurationError:
                state.llm_status = LLMStatus.CONFIGURATION_ERROR
                state.llm_error_summary = "LLM configuration is incomplete or unavailable"
                state.llm_review_error = state.llm_error_summary
                emit("AI_REVIEW", "STAGE_COMPLETED", "failed", "AI 配置不完整，未执行 AI 复核")
                emit("AI_VALIDATION", "STAGE_COMPLETED", "skipped", "AI 输出校验已跳过")
                return
            except LLMProviderError as exc:
                state.llm_status = LLMStatus.PROVIDER_ERROR
                state.validation_reason_code = None
                state.candidate_count = None
                state.valid_count = None
                state.rejected_count = None
                state.llm_error_summary = "AI 服务调用失败，本次仅保留确定性规则结果"
                state.llm_review_error = state.llm_error_summary
                details = {
                    "provider_error_code": exc.reason_code,
                    **({"http_status": exc.http_status} if exc.http_status is not None else {}),
                }
                emit("AI_REVIEW", "STAGE_COMPLETED", "failed", state.llm_error_summary, details)
                emit("AI_VALIDATION", "STAGE_COMPLETED", "skipped", "AI 输出校验已跳过")
                return
            except LLMValidationError as exc:
                state.llm_status = LLMStatus.VALIDATION_FAILED
                state.validation_reason_code = exc.reason_code
                state.candidate_count = exc.candidate_count
                state.valid_count = exc.valid_count
                state.rejected_count = exc.rejected_count
                state.llm_error_summary = str(exc)
                state.llm_review_error = state.llm_error_summary
                llm_findings.clear()
                message = (
                    "AI 结果引用证据不符合要求，相关结果已丢弃"
                    if exc.category == "evidence_reference"
                    else "AI 已返回内容，但未通过结构化格式校验"
                )
                response_details = {
                    key: value for key, value in {
                        "http_status": exc.http_status,
                        "response_character_count": exc.response_character_count,
                        "stop_reason": exc.stop_reason,
                        "content_block_count": exc.content_block_count,
                    }.items() if value is not None
                }
                emit("AI_REVIEW", "STAGE_COMPLETED", "completed", "AI 响应已返回", response_details)
                emit(
                    "AI_VALIDATION", "STAGE_COMPLETED", "failed", message,
                    {key: value for key, value in {
                        "validation_reason_code": exc.reason_code,
                        "candidate_count": exc.candidate_count,
                        "valid_count": exc.valid_count,
                        "rejected_count": exc.rejected_count,
                    }.items() if value is not None},
                )
                return
            except (TypeError, ValueError):
                state.llm_status = LLMStatus.VALIDATION_FAILED
                state.validation_reason_code = "missing_field"
                state.llm_error_summary = "候选问题缺少必填字段或字段格式无效"
                state.llm_review_error = state.llm_error_summary
                llm_findings.clear()
                emit("AI_REVIEW", "STAGE_COMPLETED", "completed", "AI 响应已返回")
                emit("AI_VALIDATION", "STAGE_COMPLETED", "failed", "AI 已返回内容，但未通过结构化格式校验", {"validation_reason_code": "missing_field"})
                return
            if not isinstance(response, LLMResponse):
                state.llm_status = LLMStatus.VALIDATION_FAILED
                state.validation_reason_code = "missing_field"
                state.llm_error_summary = "候选问题缺少必填字段或字段格式无效"
                state.llm_review_error = state.llm_error_summary
                llm_findings.clear()
                emit("AI_REVIEW", "STAGE_COMPLETED", "failed", "AI 返回结果无法处理")
                emit("AI_VALIDATION", "STAGE_COMPLETED", "failed", "AI 已返回内容，但未通过结构化格式校验", {"validation_reason_code": "missing_field"})
                return
            response_metrics = {
                key: value for key, value in {
                    "http_status": response.http_status,
                    "response_character_count": response.response_character_count,
                    "stop_reason": response.stop_reason,
                    "content_block_count": response.content_block_count,
                }.items() if value is not None
            }
            if state.llm_provider == "mock":
                emit(
                    "AI_REVIEW", "LLM_RESPONSE_RECEIVED", "completed",
                    "模拟 AI 调用链已完成，本次未执行真实 AI 业务分析",
                    {"candidate_count": len(response.findings), "provider": "mock", **response_metrics},
                )
            else:
                emit(
                    "AI_REVIEW", "LLM_RESPONSE_RECEIVED", "completed", "AI 响应已返回",
                    {"candidate_count": len(response.findings), "provider": state.llm_provider or "unknown", **response_metrics},
                )
            emit("AI_VALIDATION", "STAGE_STARTED", "running", "正在校验 AI 输出与证据引用")
            try:
                validated = validate_findings(
                    response.findings, sent_ids
                )
                constructed = [
                    Finding(
                        finding_id=f"llm-{index}",
                        origin=Origin.LLM,
                        category=item["category"],
                        severity=Severity(item["severity"]),
                        parameter=item.get("parameter"),
                        title=item["title"],
                        description=item["description"],
                        suggestion=item["suggestion"],
                        evidence_span_ids=list(item["evidence_span_ids"]),
                        needs_human_review=True,
                    )
                    for index, item in enumerate(validated)
                ]
            except (TypeError, ValueError, ValidationError) as exc:
                state.llm_status = LLMStatus.VALIDATION_FAILED
                category = exc.category if isinstance(exc, LLMValidationError) else "output_format"
                reason_code = exc.reason_code if isinstance(exc, LLMValidationError) else "missing_field"
                response_count = len(response.findings) if isinstance(response.findings, list) else None
                candidate_count = exc.candidate_count if isinstance(exc, LLMValidationError) else response_count
                valid_count = exc.valid_count if isinstance(exc, LLMValidationError) else (0 if response_count is not None else None)
                rejected_count = exc.rejected_count if isinstance(exc, LLMValidationError) else response_count
                state.validation_reason_code = reason_code
                state.candidate_count = candidate_count
                state.valid_count = valid_count
                state.rejected_count = rejected_count
                state.llm_error_summary = str(exc) if isinstance(exc, LLMValidationError) else "候选问题缺少必填字段或字段格式无效"
                state.llm_review_error = state.llm_error_summary
                state.llm_finding_count = 0
                llm_findings.clear()
                emit(
                    "AI_VALIDATION", "STAGE_COMPLETED", "failed",
                    "AI 结果引用证据不符合要求，相关结果已丢弃" if category == "evidence_reference" else "AI 已返回内容，但未通过结构化格式校验",
                    {key: value for key, value in {
                        "validation_reason_code": reason_code,
                        "candidate_count": candidate_count,
                        "valid_count": valid_count,
                        "rejected_count": rejected_count,
                    }.items() if value is not None},
                )
                return
            state.llm_status = (
                LLMStatus.COMPLETED_PARTIAL if selection.partial else LLMStatus.COMPLETED
            )
            state.llm_finding_count = len(validated)
            state.validation_reason_code = None
            state.candidate_count = len(response.findings)
            state.valid_count = len(validated)
            state.rejected_count = 0
            llm_findings.extend(constructed)
            if state.llm_provider == "mock":
                review_message = "模拟 AI 调用链已完成，本次未执行真实 AI 业务分析、AI 候选问题 0 条"
            elif selection.partial:
                review_message = "AI已复核部分重点证据，其余内容已完成确定性规则检查"
            else:
                review_message = "AI 复核调用完成"
            emit("AI_REVIEW", "STAGE_COMPLETED", "completed", review_message, {"candidate_count": len(response.findings), **response_metrics})
            emit(
                "AI_VALIDATION", "STAGE_COMPLETED", "completed",
                f"AI 输出校验完成，有效候选问题 {len(validated)} 条",
                {"valid_count": len(validated), "rejected_count": max(0, len(response.findings) - len(validated))},
            )

        def llm_reviewed_v11() -> None:
            emit("AI_EVIDENCE", "STAGE_STARTED", "running", "正在构建结构化证据包与分批审查计划")
            plan = build_evidence_plan(spans, state.facts, state.rule_results)
            packet_ledger.extend(
                [
                    {
                        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                        "run_id": state.run_id,
                        **entry,
                    }
                    for entry in plan.packet_lifecycle_entries
                ],
                summary_keys=("stage", "decision", "reason_code", "topic"),
            )
            state.packet_lifecycle_ledger = packet_ledger.to_dict()
            packet_by_id = {packet.packet_id: packet for packet in plan.packets}
            unique_selected = plan.selected_span_ids
            state.available_span_count = plan.available_span_count
            state.selected_span_count = len(unique_selected)
            state.selected_character_count = sum(batch.estimated_characters for batch in plan.batches)
            state.coverage_ratio = round(len(unique_selected) / plan.available_span_count, 4) if plan.available_span_count else 0.0
            state.batch_count = len(plan.batches)
            emit(
                "AI_EVIDENCE", "STAGE_COMPLETED", "completed" if plan.batches else "failed",
                f"已将{plan.selection_diagnostics['raw_packet_count']}个候选证据包压缩为{len(plan.packets)}个，共形成{len(plan.batches)}个安全批次",
                {
                    "available_span_count": plan.available_span_count,
                    "selected_span_count": len(unique_selected),
                    "selected_character_count": state.selected_character_count,
                    "coverage_ratio": state.coverage_ratio,
                    "ai_coverage_ratio": round(state.coverage_ratio * 100, 1),
                    "batch_count": len(plan.batches),
                },
            )
            if not plan.batches:
                state.llm_status = LLMStatus.INPUT_LIMIT_EXCEEDED
                state.llm_error_summary = "No evidence batches were available for LLM review"
                state.llm_review_error = state.llm_error_summary
                return

            raw_candidates = 0
            schema_valid = 0
            validation_rejected = 0
            batch_errors: list[str] = []
            stop_reasons: list[str] = []
            constructed: list[Finding] = []
            ai_stage_started = time.monotonic()
            provider_timeout = float(state.timeout or 120.0)
            emit("AI_REVIEW", "STAGE_STARTED", "running", "正在分批连接 AI 服务", {"provider": state.llm_provider or "unknown", "batch_count": len(plan.batches)})
            for batch_index, batch in enumerate(plan.batches, 1):
                if time.monotonic() - ai_stage_started + provider_timeout > MAX_AI_STAGE_SECONDS:
                    batch_errors.append("AI stage exceeded the 15-minute hard timeout")
                    break
                metric = {
                    "batch_id": batch.batch_id,
                    "attempt_number": 1,
                    "review_topic": batch.review_topic,
                    "packet_ids": list(batch.packet_ids),
                    "packet_source_span_ids": {pid: list(packet_by_id[pid].source_span_ids) for pid in batch.packet_ids},
                    "primary_span_ids": list(batch.primary_span_ids),
                    "source_span_count": len(batch.source_span_ids),
                    "primary_span_count": len(batch.primary_span_ids),
                    "character_count": batch.estimated_characters,
                    "candidate_count": 0,
                    "valid_count": 0,
                    "rejected_count": 0,
                    "stop_reason": None,
                    "validation_reason_code": None,
                    "llm_error_summary": None,
                }
                batch_started = time.monotonic()
                if batch_index == 1:
                    metric["selection_diagnostics"] = plan.selection_diagnostics
                try:
                    request = LLMRequest(
                        model=state.llm_model or "unknown",
                        system_prompt=(
                            "最多返回8条最重要的问题；只返回JSON数组；不得输出说明、Markdown、代码框或<think>；"
                            "无问题时只返回[]。category仅允许completeness、consistency、aggregation、"
                            "cross_domain、capacity、version_change、terminology、evidence、traceability、"
                            "unknown_scope、other；severity仅允许high、medium、low。"
                        ),
                        user_content=batch.user_content,
                        evidence_span_ids=batch.primary_span_ids,
                    )
                    try:
                        response = provider.review(request)
                    except LLMProviderError as first_error:
                        if not first_error.retryable:
                            raise
                        if time.monotonic() - ai_stage_started + provider_timeout > MAX_AI_STAGE_SECONDS:
                            raise
                        emit(
                            "AI_REVIEW", "LLM_RETRY_SCHEDULED", "partial",
                            "AI 服务调用出现可重试错误，已记录首次失败并执行一次受控重试",
                            {
                                "retry_attempt": 1,
                                "provider_error_code": first_error.reason_code,
                                **({"http_status": first_error.http_status} if first_error.http_status is not None else {}),
                            },
                        )
                        metric["attempt_number"] = 2
                        response = provider.review(request)
                    if not isinstance(response, LLMResponse):
                        raise LLMValidationError("missing_field")
                    metric.update({
                        "candidate_count": len(response.findings),
                        "http_status": response.http_status,
                        "response_character_count": response.response_character_count,
                        "stop_reason": response.stop_reason,
                        "content_block_count": response.content_block_count,
                    })
                    raw_candidates += len(response.findings)
                    if response.stop_reason:
                        stop_reasons.append(response.stop_reason)
                    validated = validate_findings(response.findings, batch.primary_span_ids)
                    metric["valid_count"] = len(validated)
                    schema_valid += len(validated)
                    validated_hashes = {
                        hashlib.sha256(
                            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        ).hexdigest()
                        for item in validated
                    }
                    for raw_index, item in enumerate(response.findings):
                        item_hash = hashlib.sha256(
                            json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        ).hexdigest()
                        candidate_ledger.append(
                            {
                                "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                                "run_id": state.run_id,
                                "batch_id": batch.batch_id,
                                "attempt_number": metric.get("attempt_number", 1),
                                "candidate_id": f"llm-b{batch_index:02d}-{raw_index}",
                                "candidate_sequence": raw_index,
                                "candidate_hash": item_hash,
                                "source_span_ids": list(item.get("evidence_span_ids", [])),
                                "schema_status": "PASS",
                                "evidence_status": "PASS" if item_hash in validated_hashes else "FAIL",
                                "protection_status": "PENDING",
                                "merge_status": "PENDING",
                                "dedup_status": "PENDING",
                                "final_finding_id": None,
                                "decision": "VALIDATED" if item_hash in validated_hashes else "DISCARDED",
                                "reason_code": None if item_hash in validated_hashes else "EVIDENCE_INVALID",
                                "stage": "VALIDATION",
                            },
                            summary_keys=("stage", "decision", "reason_code"),
                        )
                    state.ai_candidate_lifecycle_ledger = candidate_ledger.to_dict()
                    # Persist the real execution state as soon as one provider
                    # response has passed schema/evidence validation.
                    state.llm_status = LLMStatus.COMPLETED_PARTIAL
                    state.candidate_count = raw_candidates
                    state.valid_count = schema_valid
                    state.rejected_count = validation_rejected
                    state.stop_reason = response.stop_reason
                    for local_index, item in enumerate(validated):
                        expanded = expand_packet_evidence(list(item["evidence_span_ids"]), batch, packet_by_id)
                        if not expanded:
                            expanded = list(item["evidence_span_ids"])
                        constructed.append(Finding(
                            finding_id=f"llm-b{batch_index:02d}-{local_index}",
                            origin=Origin.LLM,
                            category=item["category"],
                            severity=Severity(item["severity"]),
                            parameter=item.get("parameter"),
                            title=item["title"],
                            description=item["description"],
                            suggestion=item["suggestion"],
                            evidence_span_ids=expanded,
                            needs_human_review=True,
                            original_ai_snapshot={"batch_id": batch.batch_id, "review_topic": batch.review_topic, "cited_primary_span_ids": list(item["evidence_span_ids"])},
                        ))
                        candidate_ledger.append(
                            {
                                "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                                "run_id": state.run_id,
                                "batch_id": batch.batch_id,
                                "attempt_number": metric.get("attempt_number", 1),
                                "candidate_id": f"llm-b{batch_index:02d}-{local_index}",
                                "candidate_sequence": local_index,
                                "candidate_hash": hashlib.sha256(
                                    json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                                ).hexdigest(),
                                "source_span_ids": list(expanded),
                                "schema_status": "PASS",
                                "evidence_status": "PASS",
                                "protection_status": "PENDING",
                                "merge_status": "PENDING",
                                "dedup_status": "PENDING",
                                "final_finding_id": f"llm-b{batch_index:02d}-{local_index}",
                                "decision": "CONSTRUCTED",
                                "reason_code": None,
                                "stage": "CONSTRUCTION",
                            },
                            summary_keys=("stage", "decision", "reason_code"),
                        )
                except LLMConfigurationError:
                    metric["llm_error_summary"] = "LLM configuration is incomplete or unavailable"
                    batch_errors.append(metric["llm_error_summary"])
                except LLMProviderError as exc:
                    metric["http_status"] = exc.http_status
                    metric["validation_reason_code"] = exc.reason_code
                    metric["llm_error_summary"] = "AI service connection failed"
                    batch_errors.append(metric["llm_error_summary"])
                except (LLMValidationError, ValidationError, TypeError, ValueError) as exc:
                    reason = exc.reason_code if isinstance(exc, LLMValidationError) else "missing_field"
                    rejected = exc.rejected_count if isinstance(exc, LLMValidationError) and exc.rejected_count is not None else metric["candidate_count"]
                    metric["validation_reason_code"] = reason
                    metric["rejected_count"] = rejected
                    metric["llm_error_summary"] = str(exc)
                    validation_rejected += rejected
                    batch_errors.append(f"{batch.batch_id}:{reason}")
                metric["duration_seconds"] = round(time.monotonic() - batch_started, 3)
                state.batch_metrics.append(metric)
                state.ai_candidate_lifecycle_ledger = candidate_ledger.to_dict()
                state.llm_finding_count = len(constructed)
                persist_checkpoint(constructed)
                emit(
                    "AI_REVIEW", "AI_BATCH_COMPLETED", "completed" if metric["llm_error_summary"] is None else "partial",
                    format_ai_batch_progress(batch_index, len(plan.batches)),
                    {
                        "batch_index": batch_index,
                        "batch_count": len(plan.batches),
                        "selected_span_count": len(batch.source_span_ids),
                        "selected_character_count": batch.estimated_characters,
                        "candidate_count": metric["candidate_count"],
                        "valid_count": metric["valid_count"],
                        "rejected_count": metric["rejected_count"],
                    },
                )

            guarded, guard_rejections = filter_unsupported_ai_findings(constructed, spans, self.terminology)
            state.ai_guard_rejections = guard_rejections
            llm_findings.extend(guarded)
            for rejection in guard_rejections:
                candidate_ledger.append(
                    {
                        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                        "run_id": state.run_id,
                        "batch_id": None,
                        "attempt_number": 1,
                        "candidate_id": rejection.get("finding_id"),
                        "candidate_hash": None,
                        "source_span_ids": [],
                        "schema_status": "PASS",
                        "evidence_status": "PASS",
                        "protection_status": "DISCARDED",
                        "merge_status": "NOT_APPLICABLE",
                        "dedup_status": "PENDING",
                        "final_finding_id": None,
                        "decision": "DISCARDED",
                        "reason_code": rejection.get("reason"),
                        "stage": "PROTECTION",
                    },
                    summary_keys=("stage", "decision", "reason_code"),
                )
            state.ai_candidate_lifecycle_ledger = candidate_ledger.to_dict()
            state.candidate_count = raw_candidates
            state.valid_count = schema_valid
            state.rejected_count = validation_rejected
            state.llm_finding_count = len(guarded)
            state.stop_reason = stop_reasons[0] if stop_reasons and len(set(stop_reasons)) == 1 else ("mixed" if stop_reasons else None)
            state.validation_reason_code = next((m["validation_reason_code"] for m in state.batch_metrics if m["validation_reason_code"]), None)
            state.llm_error_summary = "; ".join(dict.fromkeys(batch_errors))[:1000] or None
            state.llm_review_error = state.llm_error_summary
            state.llm_status = LLMStatus.COMPLETED_PARTIAL if batch_errors or len(unique_selected) < plan.available_span_count else LLMStatus.COMPLETED
            emit(
                "AI_REVIEW", "STAGE_COMPLETED", "completed" if not batch_errors else "partial",
                "AI分批复核完成",
                {"candidate_count": raw_candidates, "valid_count": schema_valid, "rejected_count": validation_rejected, "batch_count": len(plan.batches)},
            )
            emit(
                "AI_VALIDATION", "STAGE_COMPLETED", "completed" if not batch_errors else "partial",
                f"AI输出校验完成，通用保护丢弃 {len(guard_rejections)} 条不成立候选",
                {"valid_count": schema_valid, "rejected_count": validation_rejected, "candidate_count": raw_candidates},
            )

        def reconciled() -> None:
            emit("RECONCILIATION", "STAGE_STARTED", "running", "正在融合规则与 AI 结果")
            span_map = {span.span_id: span for span in spans}
            rule_findings = rule_results_to_findings(state.rule_results, span_map)
            state.premerge_finding_count = len(rule_findings) + len(llm_findings)
            state.findings, dedup_records = deduplicate_findings(rule_findings, llm_findings, span_map)
            state.deduplication_records = dedup_records
            for record in dedup_records:
                candidate_ledger.append(
                    {
                        "ledger_schema_version": LEDGER_SCHEMA_VERSION,
                        "run_id": state.run_id,
                        "batch_id": None,
                        "attempt_number": 1,
                        "candidate_id": record.get("deduplicated_finding_id"),
                        "candidate_hash": None,
                        "source_span_ids": [],
                        "schema_status": "PASS",
                        "evidence_status": "PASS",
                        "protection_status": "KEPT",
                        "merge_status": "DEDUPLICATED",
                        "dedup_status": "DUPLICATE",
                        "final_finding_id": record.get("kept_finding_id"),
                        "decision": "DEDUPLICATED",
                        "reason_code": "DUPLICATE_FINDING",
                        "stage": "RECONCILIATION",
                    },
                    summary_keys=("stage", "decision", "reason_code"),
                )
            state.ai_candidate_lifecycle_ledger = candidate_ledger.to_dict()
            state.deduplicated_finding_count = len(dedup_records)
            state.postmerge_finding_count = len(state.findings)
            emit(
                "RECONCILIATION", "STAGE_COMPLETED", "completed",
                f"结果融合完成，形成 {len(state.findings)} 条问题",
                {"rule_finding_count": len(rule_findings), "llm_finding_count": len(llm_findings), "final_finding_count": len(state.findings), "deduplicated_finding_count": len(dedup_records), "premerge_finding_count": state.premerge_finding_count},
            )

        def ready() -> None:
            emit("HUMAN_REVIEW", "STAGE_COMPLETED", "completed", "初审结果已准备完成，等待专家复核", {"needs_human_review": True})

        result = StageRunner().run(
            [
                (PipelineStage.UPLOADED, uploaded),
                (PipelineStage.PARSED, parsed),
                (PipelineStage.EXTRACTED, extracted),
                (PipelineStage.NORMALIZED, normalized),
                (PipelineStage.RULE_CHECKED, rule_checked),
                (
                    PipelineStage.LLM_REVIEWED,
                    lambda: llm_reviewed_v11()
                    if any(span.block_type in {BlockType.HEADING, BlockType.TABLE_CELL} for span in spans)
                    else llm_reviewed(),
                ),
                (PipelineStage.RECONCILED, reconciled),
                (PipelineStage.READY_FOR_HUMAN_REVIEW, ready),
            ]
        )
        state.stage_records = result.stage_records
        state.final_status = result.final_status
        state.rule_results = [
            item.model_copy(update={"run_id": state.run_id}) for item in state.rule_results
        ]
        state.findings = [
            item.model_copy(update={"run_id": state.run_id}) for item in state.findings
        ]
        if state.final_status == "READY_FOR_HUMAN_REVIEW":
            emit("HUMAN_REVIEW", "TASK_COMPLETED", "completed", "智能初审任务执行完成", {"final_finding_count": len(state.findings)})
        else:
            failed = next((item for item in state.stage_records if item.status == "failed"), None)
            failed_stage = failed.stage.value if failed is not None else "FAILED"
            emit(failed_stage, "TASK_FAILED", "failed", "审查任务执行失败，请检查配置或重新运行")
        return state
