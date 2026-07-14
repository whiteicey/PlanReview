from __future__ import annotations

import json
import zipfile

from app.domain.enums import Origin, ReviewStatus, Severity
from app.domain.schemas import Finding, RuleResult
from app.reports.exporters import export_anonymous_package
from app.review.pipeline import ReviewRun


def test_anonymous_allowlist_maps_adversarial_taxonomy_values_to_opaque_values(tmp_path):
    run = ReviewRun(
        "CASE-SOURCE-42",
        findings=[
            Finding(
                finding_id="F1",
                origin=Origin.RULE,
                category="vendor-A/request-abc ordinary source sentence",
                severity=Severity.HIGH,
                title="ordinary source paragraph",
                description="ordinary source paragraph body",
                suggestion="ordinary source suggestion",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
                review_status=ReviewStatus.CONFIRMED,
            )
        ],
        rule_results=[
            RuleResult(
                rule_id="R1",
                rule_version="model/request-abc",
                status="FAIL",
                severity=Severity.HIGH,
                category="vendor-A",
            )
        ],
        evidence_text_hashes={"span-1": "c" * 64},
    )

    with zipfile.ZipFile(export_anonymous_package(run, tmp_path / "anonymous.zip")) as archive:
        text = archive.read("anonymous-findings.json").decode("utf-8")
        payload = json.loads(text)

    assert payload["findings"][0]["category"] == "category-unknown"
    assert payload["findings"][0]["severity"] == "severity-0001"
    assert payload["findings"][0]["review_status"] == "reviewstatus-0002"
    assert payload["rule_versions"] == [{"rule_id": "rule-0001", "version": "version-0001"}]
    assert payload["metrics"]["review_state_counts"] == {"reviewstatus-0002": 1}
    assert payload["evidence_text_hashes"] == {"evidence-0001": "c" * 64}
    for forbidden in ("vendor", "model", "request", "CASE-SOURCE", "ordinary source"):
        assert forbidden not in text.casefold()
