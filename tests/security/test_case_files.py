from uuid import uuid4

import pytest

from app.domain.exceptions import PathTraversalError
from app.storage.case_files import store_upload
from app.storage.hashing import sha256_bytes


def test_upload_is_case_isolated_and_returns_relative_metadata(tmp_path):
    first = store_upload(tmp_path, str(uuid4()), "方案.docx", b"a")
    second = store_upload(tmp_path, str(uuid4()), "方案.docx", b"b")

    assert first.storage_relative_path != second.storage_relative_path
    assert first.sha256 != second.sha256
    assert first.sha256 == sha256_bytes(b"a")
    assert first.size == 1
    assert first.safe_name == "方案.docx"
    assert not first.storage_relative_path.startswith(str(tmp_path))
    assert (tmp_path / first.storage_relative_path).read_bytes() == b"a"
    assert (tmp_path / second.storage_relative_path).read_bytes() == b"b"


def test_upload_requires_uuid4_case_id(tmp_path):
    with pytest.raises(PathTraversalError, match="UUID4"):
        store_upload(tmp_path, "case-a", "方案.docx", b"a")


def test_upload_never_overwrites_existing_bytes(tmp_path):
    case_id = str(uuid4())
    stored = store_upload(tmp_path, case_id, "方案.docx", b"original")

    with pytest.raises(FileExistsError):
        store_upload(tmp_path, case_id, "方案.docx", b"original")

    assert (tmp_path / stored.storage_relative_path).read_bytes() == b"original"
