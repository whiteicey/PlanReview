from __future__ import annotations

from app.domain.enums import OnMissing, RuleStatus, Severity
from app.domain.schemas import RuleDefinition
from app.rules.evidence import apply_evidence_gate
from app.rules.operators import OperatorOutcome


def rule(on_missing: OnMissing, rule_id: str = "R1") -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        version="0.1",
        name="r",
        category="other",
        severity=Severity.HIGH,
        operator="evidence_required",
        on_missing=on_missing,
    )


def unknown() -> OperatorOutcome:
    return OperatorOutcome(
        RuleStatus.UNKNOWN,
        "evidence missing",
        evidence_span_ids=["s1"],
        involved_fact_ids=["f1"],
        details={"available": 0},
    )


def test_unknown_policy_preserves_status_and_traceability() -> None:
    original = unknown()

    unchanged = apply_evidence_gate(original, rule(OnMissing.UNKNOWN))
    assert unchanged is original
    assert unchanged.status is RuleStatus.UNKNOWN

    failed = apply_evidence_gate(original, rule(OnMissing.FAIL))
    assert failed is not original
    assert failed.status is RuleStatus.FAIL
    assert failed.needs_human_review is True
    assert failed.message == "evidence missing"
    assert failed.evidence_span_ids == ["s1"]
    assert failed.involved_fact_ids == ["f1"]
    assert failed.details == {"available": 0}
    assert original.status is RuleStatus.UNKNOWN
    assert original.details == {"available": 0}

    blocked = apply_evidence_gate(original, rule(OnMissing.BLOCK))
    assert blocked is not original
    assert blocked.status is RuleStatus.UNKNOWN
    assert blocked.details == {"available": 0, "blocked": True}
    assert blocked.needs_human_review is True
    assert original.details == {"available": 0}


def test_gate_never_changes_operator_pass_or_fail() -> None:
    passed = OperatorOutcome(RuleStatus.PASS, "evidence sufficient")
    failed = OperatorOutcome(RuleStatus.FAIL, "operator found a defect")

    assert apply_evidence_gate(passed, rule(OnMissing.FAIL)) is passed
    assert apply_evidence_gate(passed, rule(OnMissing.BLOCK)).status is RuleStatus.PASS
    assert apply_evidence_gate(failed, rule(OnMissing.UNKNOWN)) is failed
    assert apply_evidence_gate(failed, rule(OnMissing.BLOCK)).status is RuleStatus.FAIL
