from tests.contract.test_api import client_for, docx_bytes


def test_progress_api_never_returns_sensitive_material(monkeypatch, tmp_path):
    client = client_for(monkeypatch, tmp_path)
    upload = client.post(
        "/api/cases",
        files={"file": ("safe.docx", docx_bytes("api_key=sk-should-remain-document-data"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
    )
    run_id = client.post(f"/api/cases/{upload.json()['case_id']}/review-jobs").json()["run_id"]
    text = client.get(f"/api/runs/{run_id}/progress").text.casefold()
    for forbidden in ("sk-should", "raw_prompt", "raw_response", "traceback", "c:\\"):
        assert forbidden not in text

