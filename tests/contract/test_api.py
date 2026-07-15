from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient


def docx_bytes(text: str = "高峰产量超过处理能力") -> bytes:
    document = Document()
    document.add_paragraph(text)
    payload = BytesIO()
    document.save(payload)
    return payload.getvalue()


def client_for(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    from app.main import app

    return TestClient(app)


def test_health_has_disclaimer(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert "不是正式审查结论" in response.json()["disclaimer"]


def test_rejects_pdf_upload(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.post("/api/cases", files={"file": ("scan.pdf", b"%PDF", "application/pdf")})

    assert response.status_code == 415
    assert "仅处理文本型 DOCX" in response.json()["detail"]


def test_index_is_static(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.get("/")

    assert response.status_code == 200
    assert "AI 初审结果" in response.text


def test_upload_review_findings_patch_and_exports(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert upload.status_code == 201
    case_id = upload.json()["case_id"]

    review = client.post(f"/api/cases/{case_id}/review")
    assert review.status_code == 201
    assert review.json()["case_id"] == case_id
    assert review.json()["final_status"] == "READY_FOR_HUMAN_REVIEW"

    findings = client.get(f"/api/cases/{case_id}/findings")
    assert findings.status_code == 200
    finding = findings.json()[0]
    assert finding["evidence_span_ids"]

    patched = client.patch(
        f"/api/findings/{finding['finding_id']}",
        json={"case_id": case_id, "review_status": "confirmed", "human_note": "专家确认需要补充依据"},
    )
    assert patched.status_code == 200
    assert patched.json()["review_status"] == "confirmed"

    for format_name, media_type in (("xlsx", "spreadsheetml"), ("docx", "wordprocessingml"), ("anonymous", "zip")):
        export = client.get(f"/api/cases/{case_id}/exports/{format_name}")
        assert export.status_code == 200
        assert media_type in export.headers["content-type"]
        assert export.content
        if format_name == "anonymous":
            import json
            from zipfile import ZipFile
            with ZipFile(BytesIO(export.content)) as archive:
                anonymous = json.loads(archive.read("anonymous-findings.json"))
            assert case_id not in json.dumps(anonymous)
            assert all(value.startswith("evidence-") for value in anonymous["findings"][0]["evidence_span_ids"])


def test_expert_review_updates_status_and_note_without_overwriting_original(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    client.post(f"/api/cases/{case_id}/review")
    finding = client.get(f"/api/cases/{case_id}/findings").json()[0]
    original = {k: finding[k] for k in ("title", "description", "suggestion", "severity", "category")}

    rejected = client.patch(
        f"/api/findings/{finding['finding_id']}",
        json={"case_id": case_id, "review_status": "rejected", "human_note": "口径一致，非真实冲突"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"
    assert rejected.json()["human_note"] == "口径一致，非真实冲突"

    resolved = client.patch(
        f"/api/findings/{finding['finding_id']}",
        json={"case_id": case_id, "review_status": "resolved", "human_note": None},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["review_status"] == "resolved"
    # The original AI finding text must never be overwritten by expert review.
    for key, value in original.items():
        assert body[key] == value


def test_expert_review_rejects_invalid_status_secret_note_and_unknown_finding(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    client.post(f"/api/cases/{case_id}/review")
    finding_id = client.get(f"/api/cases/{case_id}/findings").json()[0]["finding_id"]

    invalid = client.patch(
        f"/api/findings/{finding_id}",
        json={"case_id": case_id, "review_status": "definitely-not-a-status"},
    )
    assert invalid.status_code == 422

    secret = client.patch(
        f"/api/findings/{finding_id}",
        json={"case_id": case_id, "review_status": "confirmed", "human_note": "api_key=sk-abcdefghijklmnop"},
    )
    assert secret.status_code == 422

    unknown = client.patch(
        "/api/findings/does-not-exist",
        json={"case_id": case_id, "review_status": "confirmed"},
    )
    assert unknown.status_code == 404


def test_upload_is_uuid_isolated_and_same_names_do_not_overwrite(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    first = client.post("/api/cases", files={"file": ("same.docx", docx_bytes("first"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    second = client.post("/api/cases", files={"file": ("same.docx", docx_bytes("second"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})

    assert first.status_code == second.status_code == 201
    first_data, second_data = first.json(), second.json()
    assert first_data["case_id"] != second_data["case_id"]
    assert first_data["storage_relative_path"] != second_data["storage_relative_path"]
    assert (tmp_path / "storage" / first_data["storage_relative_path"]).read_bytes() != (tmp_path / "storage" / second_data["storage_relative_path"]).read_bytes()


def test_delete_requires_recycle_and_exact_confirmation(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    created = client.post("/api/cases", files={"file": ("remove.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    case_id = created.json()["case_id"]

    assert client.request("DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}).status_code == 409
    assert client.post(f"/api/cases/{case_id}/delete-confirm").status_code == 200
    bad_confirmation = client.request("DELETE", f"/api/cases/{case_id}", json={"confirmation": "DELETE wrong"})
    assert bad_confirmation.status_code == 422
    assert client.request("DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}).status_code == 204
    assert not (tmp_path / "storage" / created.json()["storage_relative_path"]).exists()
    assert not (tmp_path / "storage" / "reports" / case_id).exists()


def test_review_and_findings_survive_repository_restart(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    created = client.post("/api/cases", files={"file": ("durable.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    case_id = created.json()["case_id"]
    assert client.post(f"/api/cases/{case_id}/review").status_code == 201

    from app.persistence.db import create_session
    from app.persistence.repository import ReviewRepository
    from app.settings import get_settings

    persisted = ReviewRepository(create_session(get_settings().db_path)).get_run(case_id)
    assert persisted is not None
    assert persisted.findings
    assert client.get(f"/api/cases/{case_id}/findings").status_code == 200
