from __future__ import annotations

from io import BytesIO
from pathlib import Path

from docx import Document
from fastapi.testclient import TestClient

from app.llm.provider import LLMRequest, LLMResponse


class FakeProvider:
    """Test-only deterministic provider bound to one requested span."""

    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, evidence_span_id: str | None = None) -> None:
        self.evidence_span_id = evidence_span_id
        self.requests: list[LLMRequest] = []

    def review(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        evidence_span_id = self.evidence_span_id or request.evidence_span_ids[0]
        return LLMResponse(
            provider="fake",
            model=request.model,
            findings=[
                {
                    "category": "capacity",
                    "severity": "high",
                    "title": "测试发现",
                    "description": "测试用确定性发现",
                    "suggestion": "补充依据",
                    "evidence_span_ids": [evidence_span_id],
                }
            ],
        )


def test_fake_provider_can_return_one_explicit_requested_span():
    provider = FakeProvider("span-2")
    response = provider.review(
        LLMRequest(
            model="fake",
            system_prompt="test",
            user_content="[span-1]\none\n\n[span-2]\ntwo",
            evidence_span_ids=["span-1", "span-2"],
        )
    )
    assert response.findings[0]["evidence_span_ids"] == ["span-2"]


def docx_bytes(text: str = "高峰产量超过处理能力") -> bytes:
    document = Document()
    document.add_paragraph(text)
    payload = BytesIO()
    document.save(payload)
    return payload.getvalue()


def client_for(monkeypatch, tmp_path: Path, provider=None) -> TestClient:
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    from app.main import app
    if provider is not None:
        import app.api.routes as routes

        monkeypatch.setattr(routes, "_build_active_provider", lambda: provider)

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


def test_rejects_unsafe_docx_with_sanitized_422(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.post(
        "/api/cases",
        files={"file": ("unsafe.docx", b"not a zip C:\\secret", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    assert response.status_code == 422
    assert response.json()["detail"] == "DOCX package structure is not supported"
    assert "secret" not in response.text


def test_config_reports_structural_limits_without_page_claim(monkeypatch, tmp_path):
    body = client_for(monkeypatch, tmp_path).get("/api/config").json()
    assert body["max_upload_bytes"] == 100 * 1024 * 1024
    assert body["max_zip_members"] == 5_000
    assert body["max_table_cells"] == 200_000
    assert "max_pages" not in body
    assert body["max_llm_spans"] == 40
    assert body["max_llm_total_characters"] == 24_000
    assert body["max_llm_single_span_characters"] == 4_000
    assert body["max_llm_evidence_ids"] == 40
    assert body["max_llm_findings"] == 8


def test_index_is_static(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    response = client.get("/")

    assert response.status_code == 200
    assert "AI 初审结果" in response.text


def test_upload_review_findings_patch_and_exports(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
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
    run_id = review.json()["run_id"]

    findings = client.get(f"/api/cases/{case_id}/findings")
    assert findings.status_code == 200
    # Completeness findings may legitimately have no direct span.  Select an
    # evidence-backed finding rather than relying on presentation order.
    finding = next(item for item in findings.json() if item["evidence_span_ids"])
    assert finding["evidence_span_ids"]

    patched = client.patch(
        f"/api/cases/{case_id}/runs/{run_id}/findings/{finding['finding_id']}",
        json={"review_status": "confirmed", "human_note": "专家确认需要补充依据"},
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


def test_expert_experience_summary_is_live_and_review_patch_returns_committed_count(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    run_id = client.post(f"/api/cases/{case_id}/review").json()["run_id"]
    finding_id = client.get(f"/api/cases/{case_id}/findings").json()[0]["finding_id"]
    endpoint = f"/api/cases/{case_id}/runs/{run_id}/findings/{finding_id}"

    initial = client.get("/api/expert-experiences/summary")
    assert initial.status_code == 200
    assert initial.json() == {"total_count": 0, "updated_at": None}

    saved = client.patch(endpoint, json={
        "review_status": "confirmed", "human_note": "专家确认", "is_expert_experience": True,
    })
    assert saved.status_code == 200
    assert saved.json()["review_saved"] is True
    assert saved.json()["expert_experience_saved"] is True
    assert saved.json()["expert_experience_total_count"] == 1

    repeated = client.patch(endpoint, json={
        "review_status": "confirmed", "human_note": "专家备注已更新", "is_expert_experience": True,
    })
    assert repeated.status_code == 200
    assert repeated.json()["expert_experience_total_count"] == 1
    live = client.get("/api/expert-experiences/summary").json()
    assert live["total_count"] == 1 and live["updated_at"] is not None

    pending = client.patch(endpoint, json={
        "review_status": "pending", "human_note": "等待补充材料", "is_expert_experience": True,
    })
    assert pending.status_code == 200
    assert pending.json()["is_expert_experience"] is False
    assert pending.json()["expert_experience_saved"] is False
    assert pending.json()["expert_experience_total_count"] == 0
    assert client.get("/api/expert-experiences/summary").json() == {"total_count": 0, "updated_at": None}


def test_expert_review_updates_status_and_note_without_overwriting_original(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    review = client.post(f"/api/cases/{case_id}/review").json()
    run_id = review["run_id"]
    finding = client.get(f"/api/cases/{case_id}/findings").json()[0]
    original = {k: finding[k] for k in ("title", "description", "suggestion", "severity", "category")}

    rejected = client.patch(
        f"/api/cases/{case_id}/runs/{run_id}/findings/{finding['finding_id']}",
        json={"review_status": "rejected", "human_note": "口径一致，非真实冲突"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["review_status"] == "rejected"
    assert rejected.json()["human_note"] == "口径一致，非真实冲突"

    resolved = client.patch(
        f"/api/cases/{case_id}/runs/{run_id}/findings/{finding['finding_id']}",
        json={"review_status": "resolved", "human_note": None},
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["review_status"] == "resolved"
    # The original AI finding text must never be overwritten by expert review.
    for key, value in original.items():
        assert body[key] == value


def test_expert_review_rejects_invalid_status_secret_note_and_unknown_finding(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    run_id = client.post(f"/api/cases/{case_id}/review").json()["run_id"]
    finding_id = client.get(f"/api/cases/{case_id}/findings").json()[0]["finding_id"]
    endpoint = f"/api/cases/{case_id}/runs/{run_id}/findings/{finding_id}"

    invalid = client.patch(
        endpoint,
        json={"review_status": "definitely-not-a-status"},
    )
    assert invalid.status_code == 422
    assert "definitely-not-a-status" not in invalid.text

    secret = client.patch(
        endpoint,
        json={"review_status": "confirmed", "human_note": "api_key=sk-abcdefghijklmnop"},
    )
    assert secret.status_code == 422
    assert secret.json()["detail"] == "专家备注疑似包含敏感凭据"
    assert "sk-" not in secret.text

    for length in (3_999, 4_000):
        accepted = client.patch(
            endpoint,
            json={"review_status": "confirmed", "human_note": "审" * length},
        )
        assert accepted.status_code == 200
    too_long = client.patch(
        endpoint,
        json={"review_status": "confirmed", "human_note": "审" * 4_001},
    )
    assert too_long.status_code == 422
    assert "专家备注最大 4000 字" in too_long.text
    assert "审" * 20 not in too_long.text

    prose = "第一段 token 为普通业务词。\n\n第二段正常说明。\n\n第三段结论。"
    accepted_prose = client.patch(
        endpoint,
        json={"review_status": "confirmed", "human_note": prose},
    )
    assert accepted_prose.status_code == 200
    assert accepted_prose.json()["human_note"] == prose

    unknown = client.patch(
        f"/api/cases/{case_id}/runs/{run_id}/findings/does-not-exist",
        json={"review_status": "confirmed"},
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
    assert client.request("DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}).status_code == 409


def test_create_database_failure_compensates_files_and_records_audit(monkeypatch, tmp_path):
    from app.persistence.models import FileOperationAuditORM
    from app.persistence.repository import ReviewRepository

    client = client_for(monkeypatch, tmp_path)
    monkeypatch.setattr(
        ReviewRepository,
        "save_case",
        lambda self, case: (_ for _ in ()).throw(OSError("C:\\secret\\review.db")),
    )
    response = client.post(
        "/api/cases",
        files={"file": ("case.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )

    assert response.status_code == 500
    assert response.json()["detail"] == "无法保存案例"
    assert not list((tmp_path / "storage" / "cases").glob("**/*.docx"))
    runtime = client.app.state.database_runtime
    with runtime.session() as session:
        events = session.query(FileOperationAuditORM).all()
    assert len(events) == 1
    assert events[0].operation == "create"
    assert events[0].recovery_required is False


def test_delete_database_failure_restores_files_and_keeps_case(monkeypatch, tmp_path):
    from app.persistence.models import FileOperationAuditORM
    from app.persistence.repository import ReviewRepository

    client = client_for(monkeypatch, tmp_path)
    created = client.post(
        "/api/cases",
        files={"file": ("case.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    case_id = created["case_id"]
    stored_path = tmp_path / "storage" / created["storage_relative_path"]
    assert client.post(f"/api/cases/{case_id}/delete-confirm").status_code == 200
    monkeypatch.setattr(
        ReviewRepository,
        "permanently_delete_case",
        lambda self, case_id, confirmation: (_ for _ in ()).throw(OSError("database unavailable")),
    )

    response = client.request(
        "DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}
    )
    assert response.status_code == 500
    assert stored_path.exists()
    runtime = client.app.state.database_runtime
    with runtime.session() as session:
        assert session.query(FileOperationAuditORM).one().recovery_required is False
        from app.persistence.models import CaseORM

        assert session.get(CaseORM, case_id) is not None


def test_delete_restore_failure_is_audited_for_recovery(monkeypatch, tmp_path):
    import app.api.routes as routes
    from app.persistence.models import FileOperationAuditORM
    from app.persistence.repository import ReviewRepository

    client = client_for(monkeypatch, tmp_path)
    created = client.post(
        "/api/cases",
        files={"file": ("case.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    case_id = created["case_id"]
    client.post(f"/api/cases/{case_id}/delete-confirm")
    monkeypatch.setattr(
        ReviewRepository,
        "permanently_delete_case",
        lambda self, case_id, confirmation: (_ for _ in ()).throw(OSError("database unavailable")),
    )
    monkeypatch.setattr(
        routes,
        "restore_quarantined_case",
        lambda quarantined: (_ for _ in ()).throw(OSError("restore unavailable")),
    )

    response = client.request(
        "DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}
    )
    assert response.status_code == 500
    runtime = client.app.state.database_runtime
    with runtime.session() as session:
        event = session.query(FileOperationAuditORM).one()
    assert event.summary == "file restore failed"
    assert event.recovery_required is True


def test_delete_cleanup_failure_records_recovery_without_restoring_case(monkeypatch, tmp_path):
    import app.api.routes as routes
    from app.persistence.models import CaseORM, FileOperationAuditORM

    client = client_for(monkeypatch, tmp_path)
    created = client.post(
        "/api/cases",
        files={"file": ("case.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    case_id = created["case_id"]
    client.post(f"/api/cases/{case_id}/delete-confirm")
    monkeypatch.setattr(
        routes,
        "cleanup_quarantine",
        lambda storage_root, quarantined: (_ for _ in ()).throw(OSError("cleanup unavailable")),
    )

    response = client.request(
        "DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}
    )
    assert response.status_code == 500
    runtime = client.app.state.database_runtime
    with runtime.session() as session:
        assert session.get(CaseORM, case_id) is None
        event = session.query(FileOperationAuditORM).one()
    assert event.summary == "file cleanup failed"
    assert event.recovery_required is True
    assert list((tmp_path / "storage" / "quarantine").glob("**/*.docx"))


def test_delete_succeeds_when_registered_case_file_is_already_missing(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    created = client.post(
        "/api/cases",
        files={"file": ("case.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    ).json()
    case_id = created["case_id"]
    (tmp_path / "storage" / created["storage_relative_path"]).unlink()
    client.post(f"/api/cases/{case_id}/delete-confirm")

    response = client.request(
        "DELETE", f"/api/cases/{case_id}", json={"confirmation": f"DELETE {case_id}"}
    )
    assert response.status_code == 204
    quarantine = tmp_path / "storage" / "quarantine"
    assert not quarantine.exists() or not any(quarantine.iterdir())


def test_review_and_findings_survive_repository_restart(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    created = client.post("/api/cases", files={"file": ("durable.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    case_id = created.json()["case_id"]
    review = client.post(f"/api/cases/{case_id}/review")
    assert review.status_code == 201

    from app.persistence.db import create_session
    from app.persistence.repository import ReviewRepository
    from app.settings import get_settings

    persisted = ReviewRepository(create_session(get_settings().db_path)).get_run(review.json()["run_id"])
    assert persisted is not None
    assert persisted.findings
    assert client.get(f"/api/cases/{case_id}/findings").status_code == 200


def test_failed_review_is_persisted_and_never_looks_like_empty_success(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]

    from app.rules.engine import RuleEngine

    def fail_rule_stage(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        raise RuntimeError("RuntimeError C:\\secret\\case.docx api_key=sk-test request_body=full document")

    monkeypatch.setattr(RuleEngine, "evaluate", fail_rule_stage)
    response = client.post(f"/api/cases/{case_id}/review")

    assert response.status_code == 422
    body = response.json()["detail"]
    assert body["case_id"] == case_id
    assert body["run_id"]
    assert body["final_status"] == "FAILED"
    assert body["failed_stage"] == "RULE_CHECKED"
    assert body["failure_detail"] == "规则校验未完成，请检查规则配置或重试。"
    response_text = response.text
    for secret in ("RuntimeError", "secret", "api_key", "sk-test", "request_body", "document", "database", "sqlite"):
        assert secret.casefold() not in response_text.casefold()

    from app.persistence.db import create_session
    from app.persistence.repository import ReviewRepository
    from app.settings import get_settings

    persisted = ReviewRepository(create_session(get_settings().db_path)).get_run(body["run_id"])
    assert persisted is not None
    assert persisted.final_status == "FAILED"
    assert any(record.stage.value == "RULE_CHECKED" and record.status == "failed" for record in persisted.stage_records)
    assert client.get(f"/api/cases/{case_id}/findings").status_code == 409
    assert client.get(f"/api/cases/{case_id}/exports/xlsx").status_code == 409


def test_fake_provider_is_span_bound_and_prompt_is_explicit(monkeypatch, tmp_path):
    provider = FakeProvider()
    client = client_for(monkeypatch, tmp_path, provider)
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes("第一段\n第二段"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    assert client.post(f"/api/cases/{case_id}/review").status_code == 201
    assert len(provider.requests) == 1
    request = provider.requests[0]
    assert request.user_content.startswith(f"[{request.evidence_span_ids[0]}]\n")
    assert all(f"[{span_id}]\n" in request.user_content for span_id in request.evidence_span_ids)
    findings = client.get(f"/api/cases/{case_id}/findings").json()
    llm_finding = next(item for item in findings if item["origin"] == "llm")
    assert llm_finding["evidence_span_ids"] == [request.evidence_span_ids[0]]


def test_fake_provider_with_unknown_span_isolated_from_review(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider("not-supplied"))
    upload = client.post(
        "/api/cases",
        files={"file": ("方案.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    response = client.post(f"/api/cases/{case_id}/review")
    assert response.status_code == 201
    assert response.json()["llm_status"] == "VALIDATION_FAILED"
    runs = client.get(f"/api/cases/{case_id}/runs").json()
    assert runs[0]["llm_status"] == "VALIDATION_FAILED"
    assert runs[0]["llm_finding_count"] == 0
    assert client.get(f"/api/cases/{case_id}/findings").status_code == 200


def test_three_review_runs_are_listed_and_expert_review_stays_on_original_run(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    created = client.post(
        "/api/cases",
        files={"file": ("history.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = created.json()["case_id"]
    first = client.post(f"/api/cases/{case_id}/review").json()
    first_finding = client.get(
        f"/api/cases/{case_id}/runs/{first['run_id']}/findings"
    ).json()[0]
    reviewed = client.patch(
        f"/api/cases/{case_id}/runs/{first['run_id']}/findings/{first_finding['finding_id']}",
        json={"review_status": "confirmed", "human_note": "line one\n\nline two"},
    )
    assert reviewed.status_code == 200

    second = client.post(f"/api/cases/{case_id}/review").json()
    third = client.post(f"/api/cases/{case_id}/review").json()
    runs = client.get(f"/api/cases/{case_id}/runs")
    assert runs.status_code == 200
    assert len(runs.json()) == 3
    assert {item["run_id"] for item in runs.json()} == {
        first["run_id"], second["run_id"], third["run_id"]
    }
    assert all(item["llm_provider"] == "fake" for item in runs.json())
    assert all(item["llm_model"] == "fake-model" for item in runs.json())
    assert all(item["llm_status"] == "COMPLETED" for item in runs.json())

    original = client.get(
        f"/api/cases/{case_id}/runs/{first['run_id']}/findings"
    ).json()[0]
    latest = client.get(
        f"/api/cases/{case_id}/runs/{third['run_id']}/findings"
    ).json()[0]
    assert original["review_status"] == "confirmed"
    assert original["human_note"] == "line one\n\nline two"
    assert latest["review_status"] == "pending"
    assert client.get(f"/api/cases/{case_id}/runs/{second['run_id']}").json()["run_id"] == second["run_id"]


def test_latest_failed_run_does_not_obscure_successful_shortcuts(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    created = client.post(
        "/api/cases",
        files={"file": ("fallback.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = created.json()["case_id"]
    successful = client.post(f"/api/cases/{case_id}/review").json()

    from app.rules.engine import RuleEngine

    monkeypatch.setattr(
        RuleEngine,
        "evaluate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("injected rule failure")),
    )
    failed_response = client.post(f"/api/cases/{case_id}/review")
    assert failed_response.status_code == 422
    failed = failed_response.json()["detail"]
    assert failed["run_id"] != successful["run_id"]

    shortcut = client.get(f"/api/cases/{case_id}/findings")
    assert shortcut.status_code == 200
    assert {item["run_id"] for item in shortcut.json()} == {successful["run_id"]}
    assert client.get(
        f"/api/cases/{case_id}/runs/{failed['run_id']}/findings"
    ).status_code == 409
    assert client.get(f"/api/cases/{case_id}/exports/xlsx").status_code == 200


def test_review_write_requires_explicit_run_and_rejects_cross_case(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, FakeProvider())
    cases = []
    for name in ("one.docx", "two.docx"):
        case_id = client.post(
            "/api/cases",
            files={"file": (name, docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        ).json()["case_id"]
        run_id = client.post(f"/api/cases/{case_id}/review").json()["run_id"]
        finding = client.get(f"/api/cases/{case_id}/runs/{run_id}/findings").json()[0]
        cases.append((case_id, run_id, finding))

    case_one, run_one, finding_one = cases[0]
    case_two, run_two, finding_two = cases[1]
    missing_run = client.patch(
        f"/api/findings/{finding_one['finding_id']}",
        json={"case_id": case_one, "review_status": "confirmed"},
    )
    assert missing_run.status_code == 422

    cross_case = client.patch(
        f"/api/cases/{case_one}/runs/{run_two}/findings/{finding_two['finding_id']}",
        json={"review_status": "rejected", "human_note": "must not write"},
    )
    assert cross_case.status_code == 404
    unchanged = client.get(f"/api/cases/{case_two}/runs/{run_two}/findings").json()[0]
    assert unchanged["review_status"] == "pending"
    assert unchanged["human_note"] is None

    unknown_in_run = client.patch(
        f"/api/cases/{case_one}/runs/{run_one}/findings/not-in-this-run",
        json={"review_status": "rejected"},
    )
    assert unknown_in_run.status_code == 404
