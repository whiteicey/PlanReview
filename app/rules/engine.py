"""Three-valued declarative rule evaluation."""

from __future__ import annotations

from copy import deepcopy

from app.domain.schemas import ParameterFact, RuleDefinition, RuleResult, SourceSpan
from app.rules.evidence import apply_evidence_gate
from app.rules.operators import OperatorContext, get_operator


class RuleEngine:
    """Evaluate enabled rules against one immutable input context."""

    def evaluate(
        self,
        rules: list[RuleDefinition],
        facts: list[ParameterFact],
        spans: list[SourceSpan],
    ) -> list[RuleResult]:
        context = OperatorContext(facts=facts, spans=spans)
        results: list[RuleResult] = []
        for rule in rules:
            if not rule.enabled:
                continue
            outcome = apply_evidence_gate(
                get_operator(rule.operator)(context, rule.params), rule
            )
            results.append(
                RuleResult(
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    status=outcome.status,
                    severity=rule.severity,
                    category=rule.category,
                    parameter=rule.params.get("parameter"),
                    message=outcome.message,
                    evidence_span_ids=list(outcome.evidence_span_ids),
                    involved_fact_ids=list(outcome.involved_fact_ids),
                    needs_human_review=(
                        outcome.needs_human_review or rule.rule_id == "VERSION-001"
                    ),
                    details=deepcopy(outcome.details),
                )
            )
        return results
