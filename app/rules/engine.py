"""Three-valued declarative rule evaluation."""

from __future__ import annotations

from copy import deepcopy
from collections.abc import Callable

from app.domain.enums import RuleStatus
from app.domain.schemas import ParameterFact, RuleDefinition, RuleResult, SourceSpan
from app.rules.evidence import apply_evidence_gate
from app.rules.operators import OperatorContext, get_operator
from app.rules.semantic import build_semantic_index


class RuleEngine:
    """Evaluate enabled rules against one immutable input context."""

    def evaluate(
        self,
        rules: list[RuleDefinition],
        facts: list[ParameterFact],
        spans: list[SourceSpan],
        *,
        observer: Callable[[str, RuleDefinition, list[RuleResult]], None] | None = None,
    ) -> list[RuleResult]:
        # Build once per run and share with only the V1.2 operators. Existing
        # operators remain behaviorally unchanged because they ignore it.
        context = OperatorContext(
            facts=facts,
            spans=spans,
            semantic_index=build_semantic_index(facts, spans),
        )
        results: list[RuleResult] = []
        for rule in rules:
            if not rule.enabled:
                continue
            if observer is not None:
                observer("started", rule, [])
            rule_results: list[RuleResult] = []
            parameters = rule.params.get("parameters")
            if isinstance(parameters, list) and parameters:
                parameter_sets = [
                    {**rule.params, "parameter": parameter} for parameter in parameters
                ]
            else:
                parameter_sets = [rule.params]
            for params in parameter_sets:
                outcome = apply_evidence_gate(get_operator(rule.operator)(context, params), rule)
                rule_results.append(
                    RuleResult(
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        status=outcome.status,
                        severity=rule.severity,
                        category=rule.category,
                        parameter=params.get("parameter"),
                        message=outcome.message,
                        evidence_span_ids=list(outcome.evidence_span_ids),
                        involved_fact_ids=list(outcome.involved_fact_ids),
                        needs_human_review=(
                            outcome.needs_human_review or rule.requires_human_review
                        ),
                        details=deepcopy(outcome.details),
                    )
                )
            results.extend(rule_results)
            if observer is not None:
                observer("completed", rule, list(rule_results))
        return results
