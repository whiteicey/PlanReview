from __future__ import annotations

from app.domain.enums import Origin, RuleStatus, Severity
from app.domain.schemas import Finding, RuleResult
from app.review.reconcile import merge_findings, rule_results_to_findings


def rr(status: RuleStatus, rule_id: str = "R1") -> RuleResult:
    return RuleResult(
        rule_id=rule_id,
        status=status,
        severity=Severity.HIGH,
        category="capacity",
        parameter="高峰产量",
        message="m",
        evidence_span_ids=["s1"],
        needs_human_review=False,
    )


def test_rule_fail_becomes_finding_and_unknown_requires_human_review() -> None:
    findings = rule_results_to_findings(
        [rr(RuleStatus.FAIL), rr(RuleStatus.UNKNOWN, "R2")], {}
    )

    assert len(findings) == 2
    assert findings[0].origin is Origin.RULE
    assert findings[0].title == "高峰产量需复核"
    assert findings[1].needs_human_review is True
    assert all(finding.evidence_span_ids == ["s1"] for finding in findings)


def test_duplicate_llm_finding_cannot_overwrite_rule_evidence_or_description() -> None:
    rule_finding = rule_results_to_findings([rr(RuleStatus.FAIL)], {})[0]
    llm = Finding(
        finding_id="L",
        origin=Origin.LLM,
        category="capacity",
        severity=Severity.LOW,
        parameter="高峰产量",
        title="高峰产量需复核",
        description="llm",
        suggestion="llm suggestion",
        evidence_span_ids=["s2", "s1"],
        needs_human_review=False,
    )

    merged = merge_findings([rule_finding], [llm])

    assert len(merged) == 1
    assert merged[0].origin is Origin.HYBRID
    assert merged[0].description == "m"
    assert merged[0].severity is Severity.HIGH
    assert merged[0].evidence_span_ids == ["s1", "s2"]
    assert merged[0].needs_human_review is True


def test_duplicate_llm_findings_are_deduplicated_without_losing_evidence() -> None:
    first = Finding(
        finding_id="L1", origin=Origin.LLM, category="capacity", severity=Severity.HIGH,
        parameter="高峰产量", title="高峰产量需复核", description="d", suggestion="s",
        evidence_span_ids=["s1"], needs_human_review=True,
    )
    second = first.model_copy(update={"finding_id": "L2", "evidence_span_ids": ["s2"]})

    merged = merge_findings([], [first, second])

    assert len(merged) == 1
    assert merged[0].origin is Origin.LLM
    assert merged[0].evidence_span_ids == ["s1", "s2"]


def test_pass_results_do_not_become_findings() -> None:
    assert rule_results_to_findings([rr(RuleStatus.PASS)], {}) == []
