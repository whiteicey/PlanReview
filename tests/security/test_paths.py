from pathlib import Path

import pytest

from app.domain.exceptions import PathTraversalError, UnsupportedFileTypeError
from app.storage.paths import safe_join, validate_upload_name


def test_safe_join_normal(tmp_path):
    path = safe_join(tmp_path, "cases", "c1", "a.docx")

    assert str(path).startswith(str(tmp_path.resolve()))


def test_safe_join_blocks_parent_escape(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "..", "..", "etc", "passwd")


def test_safe_join_blocks_absolute(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "C:\\Windows\\system32")


def test_safe_join_blocks_windows_drive_on_non_windows(tmp_path):
    with pytest.raises(PathTraversalError):
        safe_join(tmp_path, "D:escape.docx")


def test_validate_upload_name_accepts_docx():
    assert validate_upload_name("方案.docx", frozenset({".docx"})) == "方案.docx"


def test_validate_upload_name_normalizes_allowed_extension_case():
    assert validate_upload_name("方案.DOCX", frozenset({".docx"})) == "方案.DOCX"


def test_validate_upload_name_rejects_pdf():
    with pytest.raises(UnsupportedFileTypeError, match="DOCX"):
        validate_upload_name("scan.pdf", frozenset({".docx"}))


def test_validate_upload_name_strips_path():
    assert (
        validate_upload_name("../../evil.docx", frozenset({".docx"}))
        == "evil.docx"
    )
