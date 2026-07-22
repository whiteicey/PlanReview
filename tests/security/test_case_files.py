from uuid import uuid4
from io import BytesIO

import pytest
from docx import Document

from app.domain.exceptions import PathTraversalError
from app.storage.case_files import store_upload
from app.storage.hashing import sha256_bytes


def _docx_bytes(text: str) -> bytes:
    payload = BytesIO()
    document = Document()
    document.add_paragraph(text)
    document.save(payload)
    return payload.getvalue()


def test_upload_is_case_isolated_and_returns_relative_metadata(tmp_path):
    first_bytes = _docx_bytes("a")
    second_bytes = _docx_bytes("b")
    first = store_upload(tmp_path, str(uuid4()), "方案.docx", first_bytes)
    second = store_upload(tmp_path, str(uuid4()), "方案.docx", second_bytes)

    assert first.storage_relative_path != second.storage_relative_path
    assert first.sha256 != second.sha256
    assert first.sha256 == sha256_bytes(first_bytes)
    assert first.size == len(first_bytes)
    assert first.safe_name == "方案.docx"
    assert not first.storage_relative_path.startswith(str(tmp_path))
    assert (tmp_path / first.storage_relative_path).read_bytes() == first_bytes
    assert (tmp_path / second.storage_relative_path).read_bytes() == second_bytes


def test_upload_requires_uuid4_case_id(tmp_path):
    with pytest.raises(PathTraversalError, match="UUID4"):
        store_upload(tmp_path, "case-a", "方案.docx", _docx_bytes("a"))


def test_upload_never_overwrites_existing_bytes(tmp_path):
    case_id = str(uuid4())
    data = _docx_bytes("original")
    stored = store_upload(tmp_path, case_id, "方案.docx", data)

    with pytest.raises(FileExistsError):
        store_upload(tmp_path, case_id, "方案.docx", data)

    assert (tmp_path / stored.storage_relative_path).read_bytes() == data
