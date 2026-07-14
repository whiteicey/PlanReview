"""End-to-end, fail-closed review pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import Origin, PipelineStage, Severity
from app.domain.schemas import Finding, ParameterFact, RuleDefinition, RuleResult, SourceSpan, StageRecord
from app.extraction.normalization import normalize_facts_units
from app.extraction.parameters import extract_parameter_facts
from app.extraction.terminology import TerminologyMap, normalize_facts
from app.llm.provider import LLMProvider, LLMRequest, validate_findings
from app.parsers.docx_parser import ParsedDocument
from app.pipeline import StageRunner
from app.review.reconcile import merge_findings, rule_results_to_findings
from app.rules.engine import RuleEngine


@dataclass
class ReviewRun:
    """Retained review artefacts, including facts required for exact exports."""

    case_id: str
    facts: list[ParameterFact] = field(default_factory=list)
    rule_results: list[RuleResult] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    stage_records: list[StageRecord] = field(default_factory=list)
    final_status: str = "PENDING"


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

        def extracted() -> None:
            for document in documents:
                facts = extract_parameter_facts(document)
                if self.terminology is not None:
                    facts = normalize_facts(facts, self.terminology)
                raw_facts.extend(facts)

        def normalized() -> None:
            state.facts = normalize_facts_units(raw_facts)

        def rule_checked() -> None:
            state.rule_results = RuleEngine().evaluate(rules, state.facts, spans)

        def llm_reviewed() -> None:
            response = provider.review(
                LLMRequest(
                    model="mock",
                    system_prompt="只输出结构化复核意见",
                    user_content="\n".join(span.text for span in spans),
                    evidence_span_ids=[span.span_id for span in spans],
                )
            )
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
            state.findings = merge_findings(
                rule_results_to_findings(state.rule_results, span_map), llm_findings
            )

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
