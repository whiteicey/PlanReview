"""Convert rule outcomes and conservatively reconcile model findings."""

from __future__ import annotations

import re

from app.domain.enums import Origin, RuleStatus
from app.domain.schemas import Finding, RuleResult, SourceSpan


def _normalize_title(value: str) -> str:
    """Normalize textual identity without using fuzzy semantic matching."""
    return re.sub(r"[^\w]+", "", value.casefold())


def _finding_key(finding: Finding) -> tuple[str, str | None, str]:
    return (
        _normalize_title(finding.category),
        finding.parameter,
        _normalize_title(finding.title),
    )


def _rule_title(result: RuleResult) -> str:
    if result.parameter:
        return f"{result.parameter}需复核"
    return result.message or f"规则 {result.rule_id} 需复核"


def _deduplicated_evidence(*evidence_lists: list[str]) -> list[str]:
    seen: set[str] = set()
    return [
        span_id
        for evidence in evidence_lists
        for span_id in evidence
        if not (span_id in seen or seen.add(span_id))
    ]


def rule_results_to_findings(
    results: list[RuleResult], spans: dict[str, SourceSpan]
) -> list[Finding]:
    """Convert non-pass rule outcomes into evidence-preserving findings.

    ``spans`` is deliberately accepted at this boundary to keep source evidence
    available to callers; IDs are copied directly from the already-gated rule
    result rather than reconstructed or inferred.
    """
    del spans
    findings: list[Finding] = []
    for index, result in enumerate(results):
        if result.status is RuleStatus.PASS:
            continue
        findings.append(
            Finding(
                finding_id=f"rule-{result.rule_id}-{index}",
                origin=Origin.RULE,
                category=result.category,
                severity=result.severity,
                parameter=result.parameter,
                title=_rule_title(result),
                description=result.message,
                suggestion="请补充证据并由专家复核",
                rule_id=result.rule_id,
                evidence_span_ids=list(result.evidence_span_ids),
                needs_human_review=(
                    result.needs_human_review or result.status is RuleStatus.UNKNOWN
                ),
            )
        )
    return findings


def merge_findings(
    rule_findings: list[Finding], llm_findings: list[Finding]
) -> list[Finding]:
    """Merge only identity matches while retaining rule determination/evidence.

    Rule-derived findings are the authoritative record for a matching finding.
    A model may add traceable evidence IDs, but cannot replace a rule's status
    implication, severity, description, suggestion, or evidence.  A matched
    model finding marks the result hybrid and always requires human review.
    """
    merged = list(rule_findings)
    indexes = {_finding_key(finding): index for index, finding in enumerate(merged)}
    for llm_finding in llm_findings:
        key = _finding_key(llm_finding)
        index = indexes.get(key)
        if index is None:
            indexes[key] = len(merged)
            merged.append(llm_finding)
            continue

        base = merged[index]
        updates = {
            "evidence_span_ids": _deduplicated_evidence(
                base.evidence_span_ids, llm_finding.evidence_span_ids
            ),
            "needs_human_review": base.needs_human_review or llm_finding.needs_human_review,
        }
        if base.origin in {Origin.RULE, Origin.HYBRID}:
            # The rule result remains authoritative; do not let the model change
            # the assessment text, severity, recommendation, or review state.
            updates["origin"] = Origin.HYBRID
            updates["needs_human_review"] = True
        merged[index] = base.model_copy(update=updates)
    return merged
