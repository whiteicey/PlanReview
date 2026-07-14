"""Adversarial regression tests for the external DEMO golden corpus."""

from __future__ import annotations

from collections import Counter

import pytest


def _statuses(run):
    return {(result.rule_id, result.parameter): result.status.value for result in run.rule_results}


@pytest.mark.parametrize("case_id", [f"G-{index:03d}" for index in range(1, 9)])
def test_golden_statuses(case_id, expected_cases, run_golden_case):
    expected = expected_cases[case_id]
    run = run_golden_case(case_id)
    statuses = _statuses(run)

    for rule_id, expected_status in expected["expected_rules"].items():
        matches = [status for (candidate_id, _), status in statuses.items() if candidate_id == rule_id]
        assert expected_status in matches

    if case_id == "G-001":
        assert not run.findings
    if expected.get("minimum_finding_count"):
        assert len(run.findings) >= expected["minimum_finding_count"]


def test_expected_findings_preserve_classification_and_evidence_contract(
    expected_cases, run_golden_case
):
    for case_id in ("G-002", "G-003", "G-004"):
        expected = expected_cases[case_id]
        run = run_golden_case(case_id)
        for wanted in expected["expected_findings"]:
            matches = [
                finding
                for finding in run.findings
                if finding.category == wanted["category"]
                and finding.parameter == wanted.get("parameter")
                and finding.severity.value == wanted.get("severity", finding.severity.value)
            ]
            assert matches, f"{case_id} missing expected finding {wanted}"
            finding = matches[0]
            if wanted.get("must_have_evidence"):
                assert finding.evidence_span_ids
            if "needs_human_review" in wanted:
                assert finding.needs_human_review is wanted["needs_human_review"]


def test_version_suspected_is_fail_with_human_review(expected_cases, run_golden_case):
    run = run_golden_case("G-003")
    result = next(result for result in run.rule_results if result.rule_id == "VERSION-001")

    assert result.status.value == expected_cases["G-003"]["expected_rules"]["VERSION-001"]
    assert result.needs_human_review is expected_cases["G-003"]["needs_human_review"]["VERSION-001"]
    assert result.evidence_span_ids


def test_evidence_block_is_unknown_with_blocked_details(expected_cases, run_golden_case):
    run = run_golden_case("G-008")
    result = next(result for result in run.rule_results if result.rule_id == "EVIDENCE-001")
    finding = next(finding for finding in run.findings if finding.rule_id == "EVIDENCE-001")

    assert result.status.value == expected_cases["G-008"]["expected_rules"]["EVIDENCE-001"]
    assert result.details["blocked"] is expected_cases["G-008"]["blocked"]["EVIDENCE-001"]
    assert finding.needs_human_review is True
    assert finding.evidence_span_ids == []


def test_reverse_assertions_do_not_false_positive(expected_cases, run_golden_case):
    for case_id in ("G-005", "G-006"):
        run = run_golden_case(case_id)
        result = next(result for result in run.rule_results if result.rule_id == "CONSISTENCY-001")
        assert result.status.value == expected_cases[case_id]["expected_rules"]["CONSISTENCY-001"]
        assert result.status.value != "FAIL"
        assert result.needs_human_review is False


def test_convertible_units_are_normalized_before_product_comparison(expected_cases, run_golden_case):
    run = run_golden_case("G-007")
    facts = {(fact.canonical_name, fact.normalized_value, fact.canonical_unit) for fact in run.facts}
    rule_id, expected_status = next(iter(expected_cases["G-007"]["expected_rules"].items()))
    result = next(result for result in run.rule_results if result.rule_id == rule_id)

    assert ("单井设计产能", 50_000.0, "m^3/day") in facts
    assert ("总设计产能", 100_000.0, "m^3/day") in facts
    assert expected_status == "PASS"
    assert result.status.value == expected_status


def test_demo_exact_parameter_facts(run_golden_case):
    run = run_golden_case("G-004")
    values = {(fact.canonical_name, fact.normalized_value) for fact in run.facts}

    assert ("开发井总数", 40.0) in values
    assert ("生产井数", 32.0) in values
    assert ("评价/探井数", 6.0) in values
    assert ("高峰产量", 2_300_000.0) in values
    assert ("地面处理能力", 2_000_000.0) in values


def test_g009_category_summary_comes_from_corpus_and_is_reproducible(
    expected_cases, run_golden_case
):
    expected = set(expected_cases["G-009"]["expected_categories"])
    first_run = run_golden_case("G-009")
    second_run = run_golden_case("G-009")
    first = Counter(finding.category for finding in first_run.findings)
    second = Counter(finding.category for finding in second_run.findings)

    assert first == second
    assert sum(first.values()) >= expected_cases["G-009"]["minimum_finding_count"]
    assert expected <= set(first)
    assert all(isinstance(count, int) and count >= 0 for count in first.values())
