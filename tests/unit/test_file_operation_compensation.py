from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.domain.exceptions import PathTraversalError
from app.persistence.db import create_session
from app.persistence.models import FileOperationAuditORM
from app.persistence.repository import ReviewRepository
from app.storage.audit import (
    new_file_operation_event,
    persist_file_operation_event,
    warn_recovery_required,
)
from app.storage.case_files import (
    cleanup_quarantine,
    quarantine_case_storage,
    restore_quarantined_case,
)


def _event(case_id: str, *, recovery_required: bool):
    return new_file_operation_event(
        case_id,
        "delete",
        "database_commit",
        "failed",
        "file restore failed" if recovery_required else "compensation completed",
        recovery_required=recovery_required,
    )


def test_path_traversal_error_is_imported_for_all_symlink_assertions():
    assert issubclass(PathTraversalError, Exception)


def test_database_audit_is_append_only_and_detected_after_restart(tmp_path, caplog):
    db_path = tmp_path / "review.db"
    event = _event(str(uuid4()), recovery_required=True)
    session = create_session(db_path)
    persist_file_operation_event(ReviewRepository(session), tmp_path / "runtime", event)
    session.close()

    restarted = create_session(db_path)
    stored = restarted.scalar(
        select(FileOperationAuditORM).where(FileOperationAuditORM.event_id == event.event_id)
    )
    assert stored is not None
    assert stored.summary == "file restore failed"
    assert stored.recovery_required is True
    with caplog.at_level(logging.WARNING):
        assert warn_recovery_required(restarted, tmp_path / "runtime") == 1
    assert "recovery is required" in caplog.text


def test_audit_falls_back_to_compact_fsynced_jsonl(monkeypatch, tmp_path):
    class BrokenRepository:
        def append_file_operation_audit(self, _event):
            raise OSError("C:\\secret\\review.db api_key=sk-example")

    fsync_calls: list[int] = []
    monkeypatch.setattr("app.storage.audit.os.fsync", lambda descriptor: fsync_calls.append(descriptor))
    event = _event(str(uuid4()), recovery_required=True)
    persist_file_operation_event(BrokenRepository(), tmp_path / "runtime", event)

    audit_path = tmp_path / "runtime" / "audit" / "file_operations.jsonl"
    raw = audit_path.read_text(encoding="utf-8")
    record = json.loads(raw)
    assert raw.count("\n") == 1
    assert record["event_id"] == event.event_id
    assert record["recovery_required"] is True
    assert fsync_calls
    for forbidden in ("secret", "api_key", "sk-example", str(tmp_path), "traceback"):
        assert forbidden.casefold() not in raw.casefold()

    session = create_session(tmp_path / "review.db")
    assert warn_recovery_required(session, tmp_path / "runtime") == 1
    session.close()


def test_quarantine_restore_and_cleanup_have_no_orphan_files(tmp_path):
    storage = tmp_path / "storage"
    case_id = str(uuid4())
    event_id = str(uuid4())
    case_file = storage / "cases" / case_id / "documents" / "case.docx"
    report_file = storage / "reports" / case_id / "report.xlsx"
    case_file.parent.mkdir(parents=True)
    report_file.parent.mkdir(parents=True)
    case_file.write_bytes(b"case")
    report_file.write_bytes(b"report")

    quarantined = quarantine_case_storage(storage, case_id, event_id)
    assert not case_file.exists()
    assert not report_file.exists()
    restore_quarantined_case(quarantined)
    assert case_file.read_bytes() == b"case"
    assert report_file.read_bytes() == b"report"

    quarantined = quarantine_case_storage(storage, case_id, str(uuid4()))
    cleanup_quarantine(storage, quarantined)
    assert not case_file.exists()
    assert not report_file.exists()
    assert not (storage / "quarantine").exists()


def test_storage_operations_reject_symlink_components(tmp_path):
    storage = tmp_path / "storage"
    outside = tmp_path / "outside"
    outside.mkdir()
    storage.mkdir()
    try:
        (storage / "cases").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows environment")

    with pytest.raises(PathTraversalError, match="symbolic links"):
        quarantine_case_storage(storage, str(uuid4()), str(uuid4()))


def test_storage_root_parent_symlink_is_rejected_before_resolution(tmp_path):
    real_root = tmp_path / "real-root"
    real_root.mkdir()
    alias_parent = tmp_path / "alias-parent"
    try:
        alias_parent.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows environment")

    from app.storage.case_files import _checked_storage_path

    with pytest.raises(PathTraversalError, match="symbolic"):
        _checked_storage_path(alias_parent / "real-root", "cases")


def test_file_symlink_inside_case_tree_is_rejected(tmp_path):
    storage = tmp_path / "storage"
    case_dir = storage / "cases" / str(uuid4())
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    case_dir.mkdir(parents=True)
    try:
        (case_dir / "file.docx").symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows environment")

    from app.storage.case_files import remove_case_storage

    with pytest.raises(PathTraversalError, match="symbolic"):
        remove_case_storage(storage, case_dir.name)
