"""End-to-end, fail-closed review pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
import re

from app.domain.enums import BlockType, Origin, PipelineStage, Severity
from app.domain.schemas import Finding, ParameterFact, RuleDefinition, RuleResult, SourceSpan, StageRecord
from app.extraction.normalization import normalize_facts_units
from app.extraction.parameters import extract_parameter_facts
from app.extraction.terminology import TerminologyMap, normalize_facts
from app.llm.provider import LLMProvider, LLMProviderError, LLMRequest, validate_findings
from app.parsers.docx_parser import ParsedDocument
from app.pipeline import StageRunner
from app.review.reconcile import merge_findings, rule_results_to_findings
from app.rules.engine import RuleEngine


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
    ) -> ReviewRun:
        state = ReviewRun(case_id=case_id)
        spans: list[SourceSpan] = []
        raw_facts: list[ParameterFact] = []
        llm_findings: list[Finding] = []

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
            for document in documents:
                version_match = re.search(r"V(\d+(?:\.\d+)?)", document.file_name)
                source_version = version_match.group(0) if version_match else None
                facts = extract_parameter_facts(document, source_version=source_version)
                if self.terminology is not None:
                    facts = normalize_facts(facts, self.terminology)
                raw_facts.extend(facts)

        def normalized() -> None:
            state.facts = normalize_facts_units(raw_facts)

        def rule_checked() -> None:
            state.rule_results = RuleEngine().evaluate(rules, state.facts, spans)

        def llm_reviewed() -> None:
            try:
                response = provider.review(
                    LLMRequest(
                        model="mock",
                        system_prompt="只输出结构化复核意见",
                        user_content="\n".join(span.text for span in spans),
                        evidence_span_ids=[span.span_id for span in spans],
                    )
                )
            except LLMProviderError as exc:
                # An online provider failed (network/timeout/refusal/bad output).
                # Fail closed: keep the rule findings, skip the AI contribution,
                # and record that the AI pass did not complete.
                state.llm_review_error = str(exc)
                return
            try:
                validated = validate_findings(
                    response.findings, [span.span_id for span in spans]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError("LLM output failed evidence validation") from exc
            if any(not item["evidence_span_ids"] for item in validated):
                raise ValueError("LLM finding requires at least one evidence span")
            llm_findings.extend(
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
            )

        def reconciled() -> None:
            span_map = {span.span_id: span for span in spans}
            rule_findings = rule_results_to_findings(state.rule_results, span_map)
            state.findings = merge_findings(rule_findings, llm_findings)

        result = StageRunner().run(
            [
                (PipelineStage.UPLOADED, uploaded),
                (PipelineStage.PARSED, parsed),
                (PipelineStage.EXTRACTED, extracted),
                (PipelineStage.NORMALIZED, normalized),
                (PipelineStage.RULE_CHECKED, rule_checked),
                (PipelineStage.LLM_REVIEWED, llm_reviewed),
                (PipelineStage.RECONCILED, reconciled),
                (PipelineStage.READY_FOR_HUMAN_REVIEW, lambda: None),
            ]
        )
        state.stage_records = result.stage_records
        state.final_status = result.final_status
        return state
