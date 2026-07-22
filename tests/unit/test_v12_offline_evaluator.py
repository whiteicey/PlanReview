from __future__ import annotations

from scripts.evaluate_defect20_v1_2 import evaluate_run, normalize_baseline_findings


def test_evaluator_uses_rule_or_ai_chain_and_keeps_not_evaluated_out_of_fn():
    run = {
        "run_id": "run-1",
        "rule_results": [
            {"rule_id": "CROSS_SOURCE_PARAM-001", "status": "FAIL", "evidence_span_ids": ["p", "t"]}
        ],
        "batch_metrics": [],
        "packet_lifecycle_ledger": {
            "entries": [
                {"packet_id": "p1", "source_span_ids": ["p", "t"], "decision": "DROPPED"}
            ]
        },
        "ai_candidate_lifecycle_ledger": {"entries": []},
        "final_findings": [],
    }
    manifest = {
        "document_id": "doc",
        "defects": [
            {
                "defect_id": "D-1",
                "detection_type": "RULE",
                "expected_rule_id": "CROSS_SOURCE_PARAM-001",
                "target_concept": "pressure",
                "semantic_patch_record": {"baseline_source_span_id": "p"},
                "related_source_span_ids": ["t"],
            },
            {
                "defect_id": "D-2",
                "detection_type": "LLM",
                "target_concept": "schedule",
                "semantic_patch_record": {"baseline_source_span_id": "z"},
            },
        ],
    }
    result = evaluate_run(run, manifest)
    assert result["ground_truth_total"] == 2
    assert result["fn"] == 1
    assert result["not_evaluated"] == 1
    assert result["cause_counts"]["RULE_EVALUATED_AND_MISSED"] == 1
    assert result["recall"] == 0.0
    assert normalize_baseline_findings({"final_findings": []}) == []
