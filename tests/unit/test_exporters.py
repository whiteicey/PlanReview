from __future__ import annotations

import json
import zipfile

from docx import Document
from openpyxl import load_workbook

from app.domain.enums import Origin, ReviewStatus, RuleStatus, Severity
from app.domain.schemas import Finding, RuleResult
from app.reports.exporters import export_anonymous_package, export_excel, export_word
from app.review.pipeline import ReviewRun


def _run() -> ReviewRun:
    return ReviewRun(
        "CASE-1",
        findings=[
            Finding(
                finding_id="F1",
                origin=Origin.RULE,
                category="capacity",
                severity=Severity.HIGH,
                title="Capacity mismatch",
                description="The proposed capacity needs confirmation.",
                suggestion="Confirm the supporting calculation.",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
                review_status=ReviewStatus.CONFIRMED,
                human_note="专家已确认",
            )
        ],
        rule_results=[
            RuleResult(
                rule_id="CAP-001",
                status=RuleStatus.FAIL,
                severity=Severity.HIGH,
                category="capacity",
                details={"rule_version": "2026.07"},
            )
        ],
        evidence_text_hashes={"span-1": "a" * 64},
    )


def test_excel_and_word_exports_include_disclaimer_evidence_and_review_state(tmp_path):
    run = _run()
    spreadsheet = export_excel(run, tmp_path / "reports" / "findings.xlsx")
    document = export_word(run, tmp_path / "reports" / "findings.docx")

    assert spreadsheet.exists() and document.exists()
    sheet = load_workbook(spreadsheet).active
    assert "不是正式审查结论" in sheet["A1"].value
    headers = [cell.value for cell in sheet[2]]
    exported = dict(zip(headers, [cell.value for cell in sheet[3]], strict=True))
    assert exported["evidence_span_ids"] == "span-1"
    assert exported["review_status"] == "confirmed"
    assert exported["human_note"] == "专家已确认"

    text = "\n".join(paragraph.text for paragraph in Document(document).paragraphs)
    assert "不是正式审查结论" in text
    assert "span-1" in text
    assert "confirmed" in text
    assert "专家已确认" in text


def test_anonymous_package_contains_only_honest_anonymized_export_data(tmp_path):
    target = export_anonymous_package(_run(), tmp_path / "anonymous.zip")

    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["anonymous-findings.json"]
        payload = json.loads(archive.read("anonymous-findings.json"))

    assert "不是正式审查结论" in payload["disclaimer"]
    assert "case_id" not in payload
    assert payload["rule_versions"] == [{"rule_id": "rule-0001", "version": "version-0001"}]
    assert payload["evidence_text_hashes"] == {"evidence-0001": "a" * 64}
    assert payload["metrics"] == {
        "finding_count": 1,
        "review_state_counts": {"reviewstatus-0002": 1},
        "accuracy": "not_measured",
        "recall": "not_measured",
        "time_saved": "not_measured",
        "cost": "not_measured",
    }
    assert payload["findings"][0]["review_status"] == "reviewstatus-0002"
    assert payload["findings"][0]["category"] == "category-0001"
    assert payload["findings"][0]["severity"] == "severity-0001"
    assert "title" not in payload["findings"][0]
    assert "description" not in payload["findings"][0]
    assert "suggestion" not in payload["findings"][0]
    assert "human_note" not in payload["findings"][0]
