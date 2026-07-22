"""Append-only, redacted audit persistence for file/database compensation."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from sqlalchemy import select

from app.persistence.models import FileOperationAuditORM

_FILE_LOCK = RLock()
_SAFE_SUMMARIES = frozenset(
    {
        "database operation failed",
        "file cleanup failed",
        "file restore failed",
        "audit database unavailable",
        "compensation completed",
        "operation completed",
    }
)


@dataclass(frozen=True)
class FileOperationEvent:
    event_id: str
    case_id: str
    operation: str
    stage: str
    result: str
    created_at: str
    summary: str | None
    recovery_required: bool


def new_file_operation_event(
    case_id: str,
    operation: str,
    stage: str,
    result: str,
    summary: str | None,
    *,
    recovery_required: bool,
) -> FileOperationEvent:
    if summary is not None and summary not in _SAFE_SUMMARIES:
        raise ValueError("audit summary is not approved")
    return FileOperationEvent(
        event_id=str(uuid4()),
        case_id=case_id,
        operation=operation,
        stage=stage,
        result=result,
        created_at=datetime.now(timezone.utc).isoformat(),
        summary=summary,
        recovery_required=recovery_required,
    )


def persist_file_operation_event(repository, runtime_root: Path, event: FileOperationEvent) -> None:
    try:
        repository.append_file_operation_audit(event)
        return
    except Exception:
        _append_jsonl(runtime_root, event)


def _append_jsonl(runtime_root: Path, event: FileOperationEvent) -> None:
    path = Path(runtime_root) / "audit" / "file_operations.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":"))
    with _FILE_LOCK, path.open("a", encoding="utf-8", newline="\n") as output:
        output.write(line + "\n")
        output.flush()
        os.fsync(output.fileno())


def warn_recovery_required(session, runtime_root: Path) -> int:
    count = 0
    try:
        count = len(
            session.scalars(
                select(FileOperationAuditORM).where(
                    FileOperationAuditORM.recovery_required.is_(True)
                )
            ).all()
        )
    except Exception:
        session.rollback()
        logging.warning("File operation database audit requires inspection")
    path = Path(runtime_root) / "audit" / "file_operations.jsonl"
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                value = json.loads(line)
                if isinstance(value, dict) and value.get("recovery_required") is True:
                    UUID(str(value.get("event_id")))
                    count += 1
        except (OSError, ValueError, json.JSONDecodeError):
            logging.warning("File operation recovery audit requires inspection")
            return count + 1
    if count:
        logging.warning("File operation recovery is required for %d audit event(s)", count)
    return count
