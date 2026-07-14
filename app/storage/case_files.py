from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.domain.exceptions import PathTraversalError
from app.settings import get_settings
from app.storage.hashing import sha256_bytes
from app.storage.paths import safe_join, validate_upload_name


@dataclass(frozen=True)
class StoredFile:
    storage_relative_path: str
    sha256: str
    size: int
    safe_name: str


def _validate_uuid4(case_id: str) -> str:
    try:
        parsed = UUID(case_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise PathTraversalError(f"案例标识必须是 UUID4: {case_id!r}") from exc

    if parsed.version != 4 or str(parsed) != case_id.lower():
        raise PathTraversalError(f"案例标识必须是 UUID4: {case_id!r}")
    return str(parsed)


def store_upload(
    storage_root: Path, case_id: str, filename: str, data: bytes
) -> StoredFile:
    """Store one upload in its UUID4 case directory without replacing any file."""
    normalized_case_id = _validate_uuid4(case_id)
    safe_name = validate_upload_name(filename, get_settings().allowed_extensions)
    digest = sha256_bytes(data)

    root = Path(storage_root).resolve()
    documents_dir = safe_join(root, "cases", normalized_case_id, "documents")
    documents_dir.mkdir(parents=True, exist_ok=True)
    destination = safe_join(documents_dir, f"{digest}-{safe_name}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        file_descriptor = os.open(destination, flags, 0o600)
    except FileExistsError:
        raise

    try:
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(data)
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise

    relative_path = destination.relative_to(root).as_posix()
    return StoredFile(
        storage_relative_path=relative_path,
        sha256=digest,
        size=len(data),
        safe_name=safe_name,
    )
