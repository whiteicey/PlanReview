from __future__ import annotations

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from scripts.verify_release_package import (
    REQUIRED_RELEASE_FILES,
    ReleasePackageError,
    verify_release_package,
)


def _write_zip(path: Path, members: set[str]) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        for member in sorted(members):
            archive.writestr(member, b"fixture")


def test_current_source_tree_contains_required_release_files() -> None:
    root = Path(__file__).resolve().parents[2]

    result = verify_release_package(root)

    assert result.required_file_count == len(REQUIRED_RELEASE_FILES)
    assert result.archive is False


def test_demo_documentation_matches_the_bundled_release_layout() -> None:
    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    demo_guide = (root / "docs" / "DEMO.md").read_text(encoding="utf-8")
    golden_adapter = (root / "tests" / "golden" / "conftest.py").read_text(
        encoding="utf-8"
    )

    assert "12 条规则" in readme
    assert "随仓库和正式发布 ZIP 附带" in demo_guide
    assert "REVIEW_DEMO_ROOT" in demo_guide
    assert "位于仓库外" not in demo_guide
    assert "deliberately outside the repository" not in golden_adapter


def test_zip_missing_storage_sources_is_rejected(tmp_path: Path) -> None:
    archive_path = tmp_path / "missing-storage.zip"
    members = set(REQUIRED_RELEASE_FILES) - {
        "app/storage/__init__.py",
        "app/storage/audit.py",
        "app/storage/case_files.py",
        "app/storage/hashing.py",
        "app/storage/paths.py",
    }
    _write_zip(archive_path, members)

    with pytest.raises(ReleasePackageError, match="app/storage"):
        verify_release_package(archive_path)


def test_complete_zip_manifest_passes(tmp_path: Path) -> None:
    archive_path = tmp_path / "complete.zip"
    _write_zip(archive_path, set(REQUIRED_RELEASE_FILES))

    result = verify_release_package(archive_path)

    assert result.archive is True
    assert result.member_count == len(REQUIRED_RELEASE_FILES)


def test_extracted_source_verification_ignores_local_test_and_venv_artifacts(
    tmp_path: Path,
) -> None:
    for member in REQUIRED_RELEASE_FILES:
        target = tmp_path / member
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fixture")
    generated = [
        tmp_path / ".venv" / "Scripts" / "python.exe",
        tmp_path / "app" / "__pycache__" / "main.cpython-312.pyc",
        tmp_path / ".pytest_cache" / "README.md",
        tmp_path / "runtime" / "audit" / "file_operations.jsonl",
        tmp_path / "storage" / "review.db",
    ]
    for target in generated:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"generated locally")

    result = verify_release_package(tmp_path)

    assert result.archive is False
    assert result.required_file_count == len(REQUIRED_RELEASE_FILES)


@pytest.mark.parametrize(
    "forbidden",
    [
        "runtime/audit/file_operations.jsonl",
        "storage/review.db",
        ".env",
        "debug.log",
        "app/__pycache__/main.pyc",
    ],
)
def test_release_zip_rejects_runtime_and_secret_artifacts(
    tmp_path: Path, forbidden: str
) -> None:
    archive_path = tmp_path / "unsafe.zip"
    _write_zip(archive_path, set(REQUIRED_RELEASE_FILES) | {forbidden})

    with pytest.raises(ReleasePackageError, match="forbidden"):
        verify_release_package(archive_path)
