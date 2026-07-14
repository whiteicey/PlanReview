from __future__ import annotations

import json
import zipfile

from app.domain.enums import Origin, Severity
from app.domain.schemas import Finding
from app.reports.exporters import export_anonymous_package
from app.review.pipeline import ReviewRun


def test_anonymous_package_excludes_identity_source_paths_and_raw_documents(tmp_path):
    forbidden_values = {
        "provider_name": "vendor-A",
        "model_name": "model-Z",
        "base_endpoint": "https://provider.example/v1",
        "trace_token": "request-abc",
        "credential_value": "api_key=secret",
        "case_identity": "CASE-IDENTITY-42",
        "source_path": r"C:\private\source.docx",
        "raw_document": "UNREDACTED DOCUMENT BODY",
    }
    run = ReviewRun(
        forbidden_values["case_identity"],
        findings=[
            Finding(
                finding_id="F1",
                origin=Origin.RULE,
                category="c",
                severity=Severity.HIGH,
                title="Safe finding",
                description="Safe description CASE-IDENTITY-42",
                suggestion="Safe suggestion",
                evidence_span_ids=["internal-span-id"],
                needs_human_review=True,
            )
        ],
        evidence_text_hashes={"internal-span-id": "b" * 64},
    )
    # These fields emulate accidental runtime metadata and must never be serialized.
    for name, value in forbidden_values.items():
        object.__setattr__(run, name, value)

    target = export_anonymous_package(run, tmp_path / "anonymous.zip")
    with zipfile.ZipFile(target) as archive:
        assert archive.namelist() == ["anonymous-findings.json"]
        text = archive.read("anonymous-findings.json").decode("utf-8")
        payload = json.loads(text)

    assert payload["findings"][0]["evidence_span_ids"] == ["evidence-0001"]
    assert payload["findings"][0]["description"] == "[REDACTED]"
    assert payload["evidence_text_hashes"] == {"evidence-0001": "b" * 64}
    for value in forbidden_values.values():
        assert value not in text
    for secret in ("vendor", "model", "base_url", "request_id", "api_key", "docx"):
        assert secret not in text.casefold()


def test_anonymous_package_omits_hash_record_when_legacy_run_lacks_hash(tmp_path):
    run = ReviewRun(
        "CASE-1",
        findings=[
            Finding(
                finding_id="F1",
                origin=Origin.RULE,
                category="c",
                severity=Severity.HIGH,
                title="t",
                evidence_span_ids=["span-1"],
                needs_human_review=True,
            )
        ],
    )

    with zipfile.ZipFile(export_anonymous_package(run, tmp_path / "anonymous.zip")) as archive:
        payload = json.loads(archive.read("anonymous-findings.json"))
    assert payload["evidence_text_hashes"] == {}
