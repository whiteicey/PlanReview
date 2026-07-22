"""Convert rule outcomes and conservatively reconcile model findings."""

from __future__ import annotations

import re

from app.domain.enums import Origin, RuleStatus
from app.domain.schemas import Finding, RuleResult, SourceSpan

_MAX_FINDING_EVIDENCE_SPANS = 5


def _normalize_title(value: str) -> str:
    """Normalize title whitespace/case only; category identity is exact."""
    return re.sub(r"\s+", " ", value.casefold().strip())


def _finding_key(finding: Finding) -> tuple[str, str | None, str]:
    return (
        finding.category,
        finding.parameter,
        _normalize_title(finding.title),
    )


def _rule_title(result: RuleResult) -> str:
    if result.parameter:
        return f"{result.parameter}需复核"
    return result.message or f"规则 {result.rule_id} 需复核"


def _deduplicated_evidence(*evidence_lists: list[str]) -> tuple[list[str], int]:
    seen: set[str] = set()
    evidence = [
        span_id
        for evidence in evidence_lists
        for span_id in evidence
        if not (span_id in seen or seen.add(span_id))
    ]
    return evidence[:_MAX_FINDING_EVIDENCE_SPANS], max(
        0, len(evidence) - _MAX_FINDING_EVIDENCE_SPANS
    )


def _merge_evidence_metadata(finding: Finding, trimmed_count: int) -> dict:
    snapshot = dict(finding.original_ai_snapshot)
    if trimmed_count:
        snapshot["evidence_merge_trimmed_count"] = (
            int(snapshot.get("evidence_merge_trimmed_count", 0)) + trimmed_count
        )
        snapshot["evidence_limit"] = _MAX_FINDING_EVIDENCE_SPANS
    return snapshot


def _bounded_finding_evidence(finding: Finding) -> Finding:
    evidence, trimmed_count = _deduplicated_evidence(finding.evidence_span_ids)
    if not trimmed_count:
        return finding
    return finding.model_copy(
        update={
            "evidence_span_ids": evidence,
            "original_ai_snapshot": _merge_evidence_metadata(finding, trimmed_count),
        }
    )


def rule_results_to_findings(
    results: list[RuleResult], spans: dict[str, SourceSpan]
) -> list[Finding]:
    """Convert non-pass rule outcomes into evidence-preserving findings.

    ``spans`` is deliberately accepted at this boundary to keep source evidence
    available to callers; IDs are copied directly from the already-gated rule
    result rather than reconstructed or inferred.
    """
    findings: list[Finding] = []
    indexes: dict[tuple[str, str | None, str], int] = {}
    for index, result in enumerate(results):
        if result.status is RuleStatus.PASS:
            continue
        unsupported = set(result.evidence_span_ids).difference(spans)
        if unsupported:
            raise ValueError("rule result references unknown evidence span")
        finding = Finding(
            finding_id=f"rule-{result.rule_id}-{index}",
            origin=Origin.RULE,
            category=result.category,
            severity=result.severity,
            parameter=result.parameter,
            title=_rule_title(result),
            description=result.message,
            suggestion="请补充证据并由专家复核",
            rule_id=result.rule_id,
            evidence_span_ids=_deduplicated_evidence(result.evidence_span_ids)[0],
            needs_human_review=(
                result.needs_human_review or result.status is RuleStatus.UNKNOWN
            ),
        )
        key = _finding_key(finding)
        if key not in indexes:
            indexes[key] = len(findings)
            findings.append(finding)
            continue
        existing = findings[indexes[key]]
        evidence, trimmed_count = _deduplicated_evidence(
            existing.evidence_span_ids, finding.evidence_span_ids
        )
        findings[indexes[key]] = existing.model_copy(
            update={
                "evidence_span_ids": evidence,
                "needs_human_review": existing.needs_human_review or finding.needs_human_review,
                "original_ai_snapshot": _merge_evidence_metadata(existing, trimmed_count),
            }
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
    if any(not finding.evidence_span_ids for finding in llm_findings):
        raise ValueError("LLM finding requires at least one evidence span")
    merged = [_bounded_finding_evidence(finding) for finding in rule_findings]
    indexes = {_finding_key(finding): index for index, finding in enumerate(merged)}
    for llm_finding in llm_findings:
        key = _finding_key(llm_finding)
        index = indexes.get(key)
        if index is None:
            indexes[key] = len(merged)
            merged.append(_bounded_finding_evidence(llm_finding))
            continue

        base = merged[index]
        if base.origin in {Origin.RULE, Origin.HYBRID}:
            # Keep rule evidence exact. The model's evidence cannot expand the
            # supported rule conclusion; its prose is retained separately.
            supplement = llm_finding.description
            snapshot = dict(base.original_ai_snapshot)
            snapshot["llm_description"] = supplement
            snapshot["llm_suggestion"] = llm_finding.suggestion
            merged[index] = base.model_copy(
                update={
                    "origin": Origin.HYBRID,
                    "needs_human_review": True,
                    "original_ai_snapshot": snapshot,
                }
            )
        else:
            evidence, trimmed_count = _deduplicated_evidence(
                base.evidence_span_ids, llm_finding.evidence_span_ids
            )
            merged[index] = base.model_copy(
                update={
                    "evidence_span_ids": evidence,
                    "needs_human_review": base.needs_human_review or llm_finding.needs_human_review,
                    "original_ai_snapshot": _merge_evidence_metadata(base, trimmed_count),
                }
            )
    return merged
