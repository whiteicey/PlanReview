from app.api import routes
from app.llm.mock import MockProvider
from tests.contract.test_api import client_for, docx_bytes


def test_async_review_job_returns_202_and_replays_real_events(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path, MockProvider())
    upload = client.post(
        "/api/cases",
        files={"file": ("progress.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    accepted = client.post(f"/api/cases/{case_id}/review-jobs")
    assert accepted.status_code == 202
    assert accepted.json()["status"] == "RUNNING"

    run_id = accepted.json()["run_id"]
    progress = client.get(f"/api/runs/{run_id}/progress?after_sequence=0")
    assert progress.status_code == 200
    body = progress.json()
    assert body["run_status"] == "READY_FOR_HUMAN_REVIEW"
    assert body["last_sequence"] == len(body["events"])
    assert [event["sequence"] for event in body["events"]] == list(range(1, body["last_sequence"] + 1))
    assert any(event["message"] == "已读取现有解析结果" for event in body["events"])
    assert not any(event["event_type"] == "PROFILE_LOADED" for event in body["events"])
    assert any("模拟 AI 调用链已完成" in event["message"] for event in body["events"])

    summary = client.get(f"/api/cases/{case_id}/runs/{run_id}").json()
    assert summary["validation_reason_code"] is None
    assert (summary["candidate_count"], summary["valid_count"], summary["rejected_count"]) == (0, 0, 0)
    assert summary["available_span_count"] >= summary["selected_span_count"]
    assert 0 <= summary["coverage_ratio"] <= 1

    tail = client.get(f"/api/runs/{run_id}/progress?after_sequence={body['last_sequence'] - 1}").json()
    assert len(tail["events"]) == 1


def test_upload_parse_result_is_reused_without_second_parse(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("reuse.docx", docx_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    case_id = upload.json()["case_id"]
    monkeypatch.setattr(routes.DocxParser, "parse", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected reparse")))
    accepted = client.post(f"/api/cases/{case_id}/review-jobs")
    run_id = accepted.json()["run_id"]
    assert client.get(f"/api/runs/{run_id}/progress").json()["run_status"] == "READY_FOR_HUMAN_REVIEW"
