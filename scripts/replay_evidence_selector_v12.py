"""Offline V1.2 relation-packet replay and quantitative degradation gate.

This module never calls a Provider and accepts only parsed document evidence
and rule results.  It is intentionally independent from defect manifests.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from app.domain.schemas import ParameterFact, RuleResult, SourceSpan
from app.review.evidence_packets import (
    RELATION_PACKET_BUDGET_RATIO,
    build_evidence_plan,
)


def plan_metrics(plan) -> dict[str, Any]:
    diagnostics = dict(plan.selection_diagnostics or {})
    covered = set(diagnostics.get("covered_chapters") or [])
    back_half = sorted(chapter for chapter in covered if isinstance(chapter, int) and chapter >= 11)
    return {
        "packet_count": len(plan.packets),
        "source_span_count": len(plan.selected_span_ids),
        "character_count": sum(batch.estimated_characters for batch in plan.batches),
        "batch_count": len(plan.batches),
        "covered_chapters": sorted(covered),
        "covered_chapter_count": len(covered),
        "back_half_chapters": back_half,
        "back_half_chapter_count": len(back_half),
        "ordinary_packet_count": diagnostics.get("ordinary_packet_count", 0),
        "relation_packet_count": diagnostics.get("relation_packet_count", 0),
        "relation_packet_ratio": diagnostics.get("relation_packet_ratio", 0.0),
        "relation_packet_budget_ratio": diagnostics.get(
            "relation_packet_budget_ratio", RELATION_PACKET_BUDGET_RATIO
        ),
    }


def compare_metrics(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    baseline_chapters = set(baseline.get("covered_chapters") or [])
    current_chapters = set(current.get("covered_chapters") or [])
    baseline_back_half = set(baseline.get("back_half_chapters") or [])
    current_back_half = set(current.get("back_half_chapters") or [])
    chapter_ratio = (
        len(current_chapters) / len(baseline_chapters)
        if baseline_chapters
        else 1.0
    )
    back_half_ratio = (
        len(current_back_half) / len(baseline_back_half)
        if baseline_back_half
        else 1.0
    )
    relation_ratio = float(current.get("relation_packet_ratio") or 0.0)
    ordinary_count = int(current.get("ordinary_packet_count") or 0)
    batch_count = int(current.get("batch_count") or 0)
    return {
        "chapter_coverage_ratio": round(chapter_ratio, 4),
        "back_half_coverage_ratio": round(back_half_ratio, 4),
        "ordinary_budget_nonzero": ordinary_count > 0,
        "relation_ratio_within_budget": relation_ratio <= RELATION_PACKET_BUDGET_RATIO,
        "batch_count_within_limit": batch_count <= 6,
        "passed": (
            chapter_ratio >= 0.90
            and back_half_ratio >= 0.90
            and ordinary_count > 0
            and relation_ratio <= RELATION_PACKET_BUDGET_RATIO
            and batch_count <= 6
        ),
    }


def replay(
    spans: list[SourceSpan],
    facts: list[ParameterFact],
    rule_results: list[RuleResult],
    *,
    baseline_metrics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = build_evidence_plan(spans, facts, rule_results)
    metrics = plan_metrics(plan)
    return {
        "selector_version": "structured-packets-v1.2",
        "metrics": metrics,
        "gate": compare_metrics(baseline_metrics or metrics, metrics),
        "selection_diagnostics": dict(plan.selection_diagnostics or {}),
    }


def _load_input(path: Path) -> tuple[list[SourceSpan], list[ParameterFact], list[RuleResult]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    spans = [SourceSpan.model_validate(item) for item in payload.get("spans", [])]
    facts = [ParameterFact.model_validate(item) for item in payload.get("facts", [])]
    rules = [RuleResult.model_validate(item) for item in payload.get("rule_results", [])]
    return spans, facts, rules


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    spans, facts, rules = _load_input(args.input)
    baseline = (
        json.loads(args.baseline_metrics.read_text(encoding="utf-8"))
        if args.baseline_metrics
        else None
    )
    result = replay(spans, facts, rules, baseline_metrics=baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if result["gate"]["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
