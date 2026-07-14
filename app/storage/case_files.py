from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZipFile, is_zipfile

from app.domain.exceptions import PathTraversalError, UnsupportedFileTypeError
from app.settings import get_settings
from app.storage.hashing import sha256_bytes
from app.storage.paths import safe_join, validate_upload_name


class UploadTooLargeError(ValueError):
    """Raised when a streamed upload exceeds the configured byte limit."""


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


def validate_docx_package(path: Path) -> None:
    """Reject renamed PDFs and arbitrary files before a case is persisted."""
    if not is_zipfile(path):
        raise UnsupportedFileTypeError("上传内容不是有效 DOCX（仅处理文本型 DOCX）")
    try:
        with ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "word/document.xml" not in names:
                raise UnsupportedFileTypeError("DOCX 包结构无效（仅处理文本型 DOCX）")
    except (BadZipFile, OSError) as exc:
        raise UnsupportedFileTypeError("上传内容不是有效 DOCX（仅处理文本型 DOCX）") from exc


def _stored_file(root: Path, destination: Path, digest: str, size: int, safe_name: str) -> StoredFile:
    return StoredFile(
        storage_relative_path=destination.relative_to(root).as_posix(),
        sha256=digest,
        size=size,
        safe_name=safe_name,
    )


def store_upload(storage_root: Path, case_id: str, filename: str, data: bytes) -> StoredFile:
    """Store one upload in its UUID4 case directory without replacing any file."""
    normalized_case_id = _validate_uuid4(case_id)
    safe_name = validate_upload_name(filename, get_settings().allowed_extensions)
    digest = sha256_bytes(data)
    root = Path(storage_root).resolve()
    documents_dir = safe_join(root, "cases", normalized_case_id, "documents")
    documents_dir.mkdir(parents=True, exist_ok=True)
    destination = safe_join(documents_dir, f"{digest}-{safe_name}")

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    file_descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(data)
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    return _stored_file(root, destination, digest, len(data), safe_name)


async def store_upload_streaming(storage_root: Path, case_id: str, filename: str, upload, max_bytes: int) -> StoredFile:
    """Stream an UploadFile to disk, enforcing the limit before retaining it."""
    normalized_case_id = _validate_uuid4(case_id)
    safe_name = validate_upload_name(filename, get_settings().allowed_extensions)
    root = Path(storage_root).resolve()
    documents_dir = safe_join(root, "cases", normalized_case_id, "documents")
    documents_dir.mkdir(parents=True, exist_ok=True)
    temporary = safe_join(documents_dir, f".upload-{uuid4().hex}.part")
    digest = hashlib.sha256()
    size = 0
    try:
        with temporary.open("xb") as output:
            while True:
                try:
                    chunk = await upload.read(1024 * 1024)
                except TypeError:
                    chunk = await upload.read()
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise UploadTooLargeError("文件超过100MB限制")
                digest.update(chunk)
                output.write(chunk)
        validate_docx_package(temporary)
        hex_digest = digest.hexdigest()
        destination = safe_join(documents_dir, f"{hex_digest}-{safe_name}")
        if destination.exists():
            raise FileExistsError(destination)
        temporary.replace(destination)
        return _stored_file(root, destination, hex_digest, size, safe_name)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
