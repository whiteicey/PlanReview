"""Offline DEFECT20 evaluator for V1.2 runs.

The script consumes completed run exports and manifests only after review
execution.  It never imports review pipeline code that could feed a manifest
back into production review.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


CAUSES = (
    "EVIDENCE_NOT_SELECTED",
    "EVIDENCE_CONTEXT_INCOMPLETE",
    "RULE_EVALUATED_AND_MISSED",
    "AI_SAW_COMPLETE_EVIDENCE_BUT_MISSED",
    "AI_CANDIDATE_DISCARDED",
    "AI_CANDIDATE_DEDUPLICATED",
    "MATCHED_TP",
)


def _tokens(value: Any) -> set[str]:
    if not isinstance(value, str):
        return set()
    return {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,}", value)
    }


def _span_ids(item: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in ("source_span_ids", "evidence_span_ids", "baseline_source_span_ids"):
        value = item.get(key)
        if isinstance(value, list):
            ids.update(str(part) for part in value if part)
    location = item.get("location")
    if isinstance(location, dict):
        for key in ("source_span_id", "baseline_source_span_id"):
            if location.get(key):
                ids.add(str(location[key]))
    for key in ("related_locations", "related_source_span_ids"):
        value = item.get(key)
        if isinstance(value, list):
            for part in value:
                if isinstance(part, str):
                    ids.add(part)
                elif isinstance(part, dict) and part.get("source_span_id"):
                    ids.add(str(part["source_span_id"]))
    patch = item.get("semantic_patch_record")
    if isinstance(patch, dict):
        for key in ("baseline_source_span_id", "source_span_id"):
            if patch.get(key):
                ids.add(str(patch[key]))
        ids.update(str(part) for part in patch.get("related_source_span_ids", []) if part)
    return ids


def _finding_text(finding: dict[str, Any]) -> str:
    return " ".join(
        str(finding.get(key) or "")
        for key in ("title", "description", "parameter", "category", "rule_id")
    )


def _finding_score(gt: dict[str, Any], finding: dict[str, Any]) -> tuple[float, float, bool]:
    gt_text = " ".join(
        str(gt.get(key) or "")
        for key in (
            "target_concept",
            "defect_mechanism",
            "defect_description",
            "expected_finding",
            "category",
        )
    )
    text_score = (
        len(_tokens(gt_text) & _tokens(_finding_text(finding)))
        / max(1, len(_tokens(gt_text)))
    )
    gt_spans = _span_ids(gt)
    finding_spans = _span_ids(finding)
    evidence_score = (
        len(gt_spans & finding_spans) / max(1, len(gt_spans))
        if gt_spans
        else 0.0
    )
    rule_match = bool(
        gt.get("expected_rule_id")
        and gt.get("expected_rule_id") == finding.get("rule_id")
    )
    score = 0.45 * text_score + 0.45 * evidence_score + (0.10 if rule_match else 0.0)
    complete = text_score >= 0.45 and evidence_score >= (0.5 if gt_spans else 0.0)
    return score, evidence_score, complete


def _ledger_entries(run: dict[str, Any], key: str) -> list[dict[str, Any]]:
    ledger = run.get(key)
    if isinstance(ledger, dict) and isinstance(ledger.get("entries"), list):
        return [entry for entry in ledger["entries"] if isinstance(entry, dict)]
    return []


def _entered_chains(gt: dict[str, Any], run: dict[str, Any]) -> dict[str, bool]:
    spans = _span_ids(gt)
    rule_entries = run.get("rule_results") or run.get("rules") or []
    rule_entered = False
    expected_rule = gt.get("expected_rule_id")
    if expected_rule and isinstance(rule_entries, list):
        for result in rule_entries:
            if not isinstance(result, dict) or result.get("rule_id") != expected_rule:
                continue
            result_spans = _span_ids(result)
            if not spans or spans & result_spans or result.get("status") in {"PASS", "FAIL", "UNKNOWN"}:
                rule_entered = True
                break
    ai_entered = False
    for metric in run.get("batch_metrics") or []:
        if not isinstance(metric, dict):
            continue
        packet_sources = metric.get("packet_source_span_ids") or {}
        batch_spans = set(
            str(span_id)
            for packet_span_ids in packet_sources.values()
            for span_id in (packet_span_ids or [])
        )
        batch_spans.update(str(item) for item in metric.get("primary_span_ids", []) if item)
        if spans and spans.issubset(batch_spans):
            ai_entered = True
            break
    if not ai_entered:
        selected_entries = [
            entry for entry in _ledger_entries(run, "packet_lifecycle_ledger")
            if entry.get("decision") == "SELECTED" and entry.get("batch_id")
        ]
        for entry in selected_entries:
            entry_spans = set(str(item) for item in entry.get("source_span_ids", []) if item)
            if spans and spans.issubset(entry_spans):
                ai_entered = True
                break
    return {
        "evidence_entered_rule_chain": rule_entered,
        "evidence_entered_ai_chain": ai_entered,
        "evidence_entered_detection_chain": rule_entered or ai_entered,
    }


def _miss_cause(gt: dict[str, Any], run: dict[str, Any], chain: dict[str, bool]) -> str:
    gt_spans = _span_ids(gt)
    if chain.get("evidence_entered_rule_chain"):
        return "RULE_EVALUATED_AND_MISSED"
    candidate_entries = _ledger_entries(run, "ai_candidate_lifecycle_ledger")
    for entry in candidate_entries:
        candidate_spans = set(str(item) for item in entry.get("source_span_ids", []) if item)
        if gt_spans and gt_spans.issubset(candidate_spans):
            decision = str(entry.get("decision") or "").upper()
            merge = str(entry.get("merge_status") or "").upper()
            dedup = str(entry.get("dedup_status") or "").upper()
            if "DEDUP" in decision or "DEDUP" in merge or "DUPLICATE" in dedup:
                return "AI_CANDIDATE_DEDUPLICATED"
            if decision == "DISCARDED" or entry.get("protection_status") == "DISCARDED":
                return "AI_CANDIDATE_DISCARDED"
    packet_entries = _ledger_entries(run, "packet_lifecycle_ledger")
    if gt_spans:
        partial_packet = False
        for entry in packet_entries:
            packet_spans = set(str(item) for item in entry.get("source_span_ids", []) if item)
            if gt_spans & packet_spans:
                if gt_spans.issubset(packet_spans):
                    if entry.get("decision") not in {"SELECTED"}:
                        return "EVIDENCE_NOT_SELECTED"
                else:
                    partial_packet = True
        if partial_packet:
            return "EVIDENCE_CONTEXT_INCOMPLETE"
    if chain.get("evidence_entered_ai_chain"):
        return "AI_SAW_COMPLETE_EVIDENCE_BUT_MISSED"
    return "EVIDENCE_NOT_SELECTED"


def normalize_baseline_findings(run: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a stable baseline instance snapshot without finding-ID matching."""
    normalized: list[dict[str, Any]] = []
    for finding in run.get("final_findings") or run.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        normalized.append(
            {
                "instance_key": hashlib.sha256(
                    json.dumps(
                        {
                            "origin": finding.get("origin"),
                            "rule_id": finding.get("rule_id"),
                            "category": finding.get("category"),
                            "parameter": finding.get("parameter"),
                            "evidence_span_ids": sorted(_span_ids(finding)),
                            "title_tokens": sorted(_tokens(finding.get("title"))),
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest(),
                "finding_id": finding.get("finding_id"),
                "origin": finding.get("origin"),
                "rule_id": finding.get("rule_id"),
                "category": finding.get("category"),
                "parameter": finding.get("parameter"),
                "evidence_span_ids": sorted(_span_ids(finding)),
                "title": finding.get("title"),
                "description": finding.get("description"),
            }
        )
    return normalized


def _baseline_match(finding: dict[str, Any], baseline: list[dict[str, Any]]) -> dict[str, Any] | None:
    for existing in baseline:
        _, evidence_score, _ = _finding_score(existing, finding)
        same_rule = existing.get("rule_id") and existing.get("rule_id") == finding.get("rule_id")
        same_parameter = existing.get("parameter") and existing.get("parameter") == finding.get("parameter")
        if evidence_score >= 0.5 and (same_rule or same_parameter):
            return existing
    return None


def evaluate_run(
    run: dict[str, Any],
    manifest: dict[str, Any],
    *,
    baseline_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    baseline_findings = baseline_findings or []
    ground_truth = list(manifest.get("defects") or [])
    final_findings = [
        item for item in (run.get("final_findings") or run.get("findings") or [])
        if isinstance(item, dict)
    ]
    ai_candidates = [
        item for item in (run.get("ai_candidates") or run.get("candidate_findings") or [])
        if isinstance(item, dict)
    ]
    matched_finding_ids: set[str] = set()
    ground_truth_rows: list[dict[str, Any]] = []
    finding_rows: list[dict[str, Any]] = []
    for gt in ground_truth:
        chain = _entered_chains(gt, run)
        candidates: list[tuple[float, float, bool, dict[str, Any]]] = []
        for finding in final_findings:
            if finding.get("finding_id") in matched_finding_ids:
                continue
            score, evidence_score, complete = _finding_score(gt, finding)
            if score >= 0.35:
                candidates.append((score, evidence_score, complete, finding))
        candidates.sort(key=lambda item: (-item[0], item[3].get("finding_id") or ""))
        selected = candidates[0] if candidates else None
        if selected and selected[2]:
            classification = "TP"
            cause = "MATCHED_TP"
            matched_finding_ids.add(selected[3].get("finding_id"))
        elif selected:
            classification = "PARTIAL"
            cause = "AI_SAW_COMPLETE_EVIDENCE_BUT_MISSED"
            matched_finding_ids.add(selected[3].get("finding_id"))
        elif not chain["evidence_entered_detection_chain"]:
            classification = "NOT_EVALUATED"
            cause = "EVIDENCE_NOT_SELECTED"
        else:
            classification = "FN"
            cause = _miss_cause(gt, run, chain)
        ground_truth_rows.append(
            {
                "defect_id": gt.get("defect_id"),
                "classification": classification,
                "cause": cause,
                **chain,
                "matched_finding_id": selected[3].get("finding_id") if selected else None,
                "match_score": round(selected[0], 4) if selected else 0.0,
                "evidence_score": round(selected[1], 4) if selected else 0.0,
                "detection_type": gt.get("detection_type"),
            }
        )
    for finding in final_findings:
        finding_id = finding.get("finding_id")
        if finding_id in matched_finding_ids:
            classification = "MATCHED_TP"
        elif _baseline_match(finding, baseline_findings):
            classification = "BASELINE_EXISTING"
        else:
            classification = "FP"
        finding_rows.append(
            {
                "finding_id": finding_id,
                "classification": classification,
                "origin": finding.get("origin"),
                "rule_id": finding.get("rule_id"),
                "evidence_span_ids": sorted(_span_ids(finding)),
                "title": finding.get("title"),
                "description": finding.get("description"),
            }
        )
    counts = Counter(row["classification"] for row in ground_truth_rows)
    finding_counts = Counter(row["classification"] for row in finding_rows)
    tp = counts["TP"]
    partial = counts["PARTIAL"]
    fn = counts["FN"]
    not_evaluated = counts["NOT_EVALUATED"]
    fp = finding_counts["FP"]
    evaluable = tp + partial + fn
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / evaluable if evaluable else None
    tp_evidence = [
        row for row in ground_truth_rows
        if row["classification"] == "TP"
    ]
    evidence_accuracy = (
        sum(row["evidence_score"] >= 0.5 for row in tp_evidence) / len(tp_evidence)
        if tp_evidence else None
    )
    return {
        "run_id": run.get("run_id"),
        "document_id": manifest.get("document_id"),
        "ground_truth_total": len(ground_truth),
        "tp": tp,
        "partial": partial,
        "fn": fn,
        "not_evaluated": not_evaluated,
        "fp": fp,
        "baseline_existing": finding_counts["BASELINE_EXISTING"],
        "duplicate": finding_counts["DUPLICATE"],
        "valid_additional": finding_counts["VALID_ADDITIONAL"],
        "precision": precision,
        "recall": recall,
        "evidence_accuracy": evidence_accuracy,
        "evaluable_count": evaluable,
        "rule_tp": sum(row["classification"] == "TP" and row["detection_type"] == "RULE" for row in ground_truth_rows),
        "hybrid_tp": sum(row["classification"] == "TP" and row["detection_type"] == "HYBRID" for row in ground_truth_rows),
        "llm_tp": sum(row["classification"] == "TP" and row["detection_type"] == "LLM" for row in ground_truth_rows),
        "ground_truth": ground_truth_rows,
        "findings": finding_rows,
        "cause_counts": dict(Counter(row["cause"] for row in ground_truth_rows)),
        "rule_metrics": run.get("rule_metrics") or {},
        "evidence_coverage": {
            "rule_chain": sum(row["evidence_entered_rule_chain"] for row in ground_truth_rows),
            "ai_chain": sum(row["evidence_entered_ai_chain"] for row in ground_truth_rows),
            "detection_chain": sum(row["evidence_entered_detection_chain"] for row in ground_truth_rows),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline-snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run = json.loads(args.run.read_text(encoding="utf-8"))
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    baseline = (
        json.loads(args.baseline_snapshot.read_text(encoding="utf-8"))
        if args.baseline_snapshot
        else []
    )
    if isinstance(baseline, dict):
        baseline = baseline.get("findings", [])
    result = evaluate_run(run, manifest, baseline_findings=baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
