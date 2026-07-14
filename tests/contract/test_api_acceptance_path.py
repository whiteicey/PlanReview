from __future__ import annotations

from io import BytesIO

from docx import Document
from fastapi.testclient import TestClient


def _docx() -> bytes:
    document = Document()
    document.add_paragraph("高峰产量超过处理能力")
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def test_config_is_safe_and_large_upload_is_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    from app.main import app

    client = TestClient(app)
    config = client.get("/api/config")
    assert config.status_code == 200
    assert config.json()["allowed_extensions"] == [".docx"]
    assert all("key" not in field.casefold() for field in config.json())

    class OverLimitFile:
        filename = "large.docx"
        content_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

        async def read(self):
            return b"x" * (100 * 1024 * 1024 + 1)

    from app.api.routes import create_case
    from fastapi import HTTPException
    import pytest

    with pytest.raises(HTTPException) as error:
        import asyncio
        asyncio.run(create_case(OverLimitFile()))
    assert error.value.status_code == 413


def test_invalid_export_and_unknown_case_are_not_successful(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    from app.main import app

    client = TestClient(app)
    assert client.post("/api/cases/not-a-uuid/review").status_code == 422
    assert client.get("/api/cases/not-a-uuid/exports/pdf").status_code == 422
