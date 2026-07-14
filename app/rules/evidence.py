"""Evidence gate for preserving three-valued rule outcomes."""

from __future__ import annotations

from dataclasses import replace

from app.domain.enums import OnMissing, RuleStatus
from app.domain.schemas import RuleDefinition
from app.rules.operators import OperatorOutcome


def apply_evidence_gate(
    outcome: OperatorOutcome, rule: RuleDefinition
) -> OperatorOutcome:
    """Apply a rule's missing-evidence policy without fabricating a PASS.

    Only an operator's UNKNOWN outcome is policy-dependent.  A conclusive PASS
    or FAIL remains the operator's decision.  ``replace`` makes a new frozen
    outcome and copies the details mapping before the block marker is added.
    """
    if outcome.status is not RuleStatus.UNKNOWN:
        return outcome
    if rule.on_missing is OnMissing.FAIL:
        return replace(
            outcome,
            status=RuleStatus.FAIL,
            needs_human_review=True,
        )
    if rule.on_missing is OnMissing.BLOCK:
        return replace(
            outcome,
            needs_human_review=True,
            details={**outcome.details, "blocked": True},
        )
    return outcome
