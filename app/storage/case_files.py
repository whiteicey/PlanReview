from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from uuid import UUID, uuid4
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, is_zipfile

from app.domain.exceptions import (
    DocxResourceLimitError,
    PathTraversalError,
    UnsafeDocxPackageError,
    UnsupportedFileTypeError,
)
from app.settings import Settings, get_settings
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


@dataclass(frozen=True)
class StagedFile:
    temporary_path: Path
    sha256: str
    size: int
    safe_name: str
    case_id: str


@dataclass(frozen=True)
class QuarantinedCase:
    event_id: str
    moves: tuple[tuple[Path, Path], ...]
    storage_root: Path


def _validate_uuid4(case_id: str) -> str:
    try:
        parsed = UUID(case_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise PathTraversalError(f"案例标识必须是 UUID4: {case_id!r}") from exc

    if parsed.version != 4 or str(parsed) != case_id.lower():
        raise PathTraversalError(f"案例标识必须是 UUID4: {case_id!r}")
    return str(parsed)


def _validate_uuid(value: str, label: str) -> str:
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as exc:
        raise PathTraversalError(f"{label} must be a UUID") from exc
    if str(parsed) != value.lower():
        raise PathTraversalError(f"{label} must be a canonical UUID")
    return str(parsed)


def _checked_storage_path(storage_root: Path, *parts: str) -> tuple[Path, Path]:
    """Return a path below storage after rejecting symlinked path components."""
    lexical_root = Path(storage_root).expanduser().absolute()
    _reject_existing_symlink_components(lexical_root, root_error=True)
    lexical_root.mkdir(parents=True, exist_ok=True)
    lexical_candidate = lexical_root.joinpath(*parts)
    _reject_existing_symlink_components(lexical_candidate)
    root = lexical_root.resolve()
    candidate = safe_join(root, *parts)
    return root, candidate


def _reject_existing_symlink_components(path: Path, *, root_error: bool = False) -> None:
    """Check lexical path components before any ``resolve`` follows them."""
    for component in (path, *path.parents):
        if component.exists() and component.is_symlink():
            if root_error:
                raise PathTraversalError("storage root must not be a symbolic link")
            raise PathTraversalError("symbolic links are not allowed in case storage")


def _prune_empty_directories(path: Path, stop: Path) -> None:
    current = path
    while current != stop:
        try:
            current.rmdir()
        except (FileNotFoundError, OSError):
            return
        current = current.parent


def _reject_symlinks_in_tree(path: Path) -> None:
    if not path.exists():
        return
    if path.is_symlink():
        raise PathTraversalError("symbolic links are not allowed in case storage")
    for current_root, directories, files in os.walk(path, followlinks=False):
        current = Path(current_root)
        for name in (*directories, *files):
            if (current / name).is_symlink():
                raise PathTraversalError("symbolic links are not allowed in case storage")


_DOCX_CORE_MEMBERS = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
_UNSUPPORTED_EMBEDDED_PREFIXES = ("word/activex/", "word/embeddings/")
_UNSUPPORTED_EMBEDDED_NAMES = {"word/vbaproject.bin", "word/vbadata.xml"}


def _safe_archive_member_name(name: str) -> str:
    if not name or "\x00" in name or "\\" in name:
        raise UnsafeDocxPackageError("DOCX package structure is not supported")
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if (
        posix.is_absolute()
        or windows.is_absolute()
        or windows.drive
        or windows.root
        or ".." in posix.parts
        or "." in posix.parts
    ):
        raise UnsafeDocxPackageError("DOCX package structure is not supported")
    return posix.as_posix().rstrip("/")


def validate_docx_package(path: Path, limits: Settings | None = None) -> None:
    """Preflight all ZIP members without rejecting safe unknown Office extensions."""
    limits = limits or get_settings()
    if not is_zipfile(path):
        raise UnsafeDocxPackageError("DOCX package structure is not supported")
    try:
        with ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_members:
                raise DocxResourceLimitError("DOCX archive exceeds configured limits")
            names: set[str] = set()
            total_uncompressed = 0
            for info in infos:
                name = _safe_archive_member_name(info.filename)
                if name in names:
                    raise UnsafeDocxPackageError("DOCX package structure is not supported")
                names.add(name)
                if info.flag_bits & 0x1 or info.compress_type not in (ZIP_STORED, ZIP_DEFLATED):
                    raise UnsafeDocxPackageError("DOCX package structure is not supported")
                lowered = name.casefold()
                if lowered in _UNSUPPORTED_EMBEDDED_NAMES or lowered.startswith(
                    _UNSUPPORTED_EMBEDDED_PREFIXES
                ):
                    raise UnsafeDocxPackageError("DOCX embedded objects are not supported")
                if info.file_size > limits.max_zip_member_bytes:
                    raise DocxResourceLimitError("DOCX archive exceeds configured limits")
                total_uncompressed += info.file_size
                if total_uncompressed > limits.max_zip_uncompressed_bytes:
                    raise DocxResourceLimitError("DOCX archive exceeds configured limits")
                if (
                    info.file_size
                    and info.file_size / max(info.compress_size, 1)
                    > limits.max_zip_compression_ratio
                ):
                    raise DocxResourceLimitError("DOCX archive exceeds configured limits")
            if not _DOCX_CORE_MEMBERS.issubset(names):
                raise UnsafeDocxPackageError("DOCX package structure is not supported")
    except (BadZipFile, OSError) as exc:
        raise UnsafeDocxPackageError("DOCX package structure is not supported") from exc


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
    if len(data) > get_settings().max_upload_bytes:
        raise UploadTooLargeError("upload exceeds configured size limit")
    if data.startswith(b"%PDF-"):
        raise UnsupportedFileTypeError("仅处理文本型 DOCX")
    digest = sha256_bytes(data)
    root, documents_dir = _checked_storage_path(
        storage_root, "cases", normalized_case_id, "documents"
    )
    documents_dir.mkdir(parents=True, exist_ok=True)
    _, destination = _checked_storage_path(
        root, "cases", normalized_case_id, "documents", f"{digest}-{safe_name}"
    )

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    file_descriptor = os.open(destination, flags, 0o600)
    try:
        with os.fdopen(file_descriptor, "wb") as output:
            output.write(data)
        validate_docx_package(destination)
    except BaseException:
        try:
            destination.unlink()
        except FileNotFoundError:
            pass
        raise
    return _stored_file(root, destination, digest, len(data), safe_name)


async def store_upload_streaming(storage_root: Path, case_id: str, filename: str, upload, max_bytes: int) -> StoredFile:
    """Compatibility wrapper around the staged, atomic upload workflow."""
    staged = await stage_upload_streaming(storage_root, case_id, filename, upload, max_bytes)
    try:
        return finalize_staged_upload(storage_root, staged)
    except BaseException:
        discard_staged_upload(storage_root, staged)
        raise


async def stage_upload_streaming(
    storage_root: Path,
    case_id: str,
    filename: str,
    upload,
    max_bytes: int,
) -> StagedFile:
    """Stream and validate an upload in storage-local staging."""
    normalized_case_id = _validate_uuid4(case_id)
    safe_name = validate_upload_name(filename, get_settings().allowed_extensions)
    root, staging_dir = _checked_storage_path(storage_root, "staging", normalized_case_id)
    staging_dir.mkdir(parents=True, exist_ok=True)
    _, temporary = _checked_storage_path(root, "staging", normalized_case_id, f"upload-{uuid4().hex}.part")
    digest = hashlib.sha256()
    size = 0
    signature = bytearray()
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
                    raise UploadTooLargeError("upload exceeds configured size limit")
                if len(signature) < 5:
                    signature.extend(chunk[: 5 - len(signature)])
                    if len(signature) >= 5 and bytes(signature).startswith(b"%PDF-"):
                        raise UnsupportedFileTypeError("仅处理文本型 DOCX")
                digest.update(chunk)
                output.write(chunk)
        validate_docx_package(temporary)
        return StagedFile(
            temporary_path=temporary,
            sha256=digest.hexdigest(),
            size=size,
            safe_name=safe_name,
            case_id=normalized_case_id,
        )
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        _prune_empty_directories(staging_dir, root)
        raise


def finalize_staged_upload(storage_root: Path, staged: StagedFile) -> StoredFile:
    root, expected_staging = _checked_storage_path(
        storage_root, "staging", staged.case_id, staged.temporary_path.name
    )
    if staged.temporary_path != expected_staging or not expected_staging.is_file():
        raise PathTraversalError("staged upload is outside managed storage")
    _, documents_dir = _checked_storage_path(root, "cases", staged.case_id, "documents")
    documents_dir.mkdir(parents=True, exist_ok=True)
    _, destination = _checked_storage_path(
        root, "cases", staged.case_id, "documents", f"{staged.sha256}-{staged.safe_name}"
    )
    if destination.exists():
        raise FileExistsError("case file already exists")
    expected_staging.replace(destination)
    _prune_empty_directories(expected_staging.parent, root)
    return _stored_file(root, destination, staged.sha256, staged.size, staged.safe_name)


def discard_staged_upload(storage_root: Path, staged: StagedFile) -> None:
    root, expected = _checked_storage_path(
        storage_root, "staging", staged.case_id, staged.temporary_path.name
    )
    if staged.temporary_path != expected:
        raise PathTraversalError("staged upload is outside managed storage")
    expected.unlink(missing_ok=True)
    _prune_empty_directories(expected.parent, root)


def remove_case_storage(storage_root: Path, case_id: str) -> None:
    normalized = _validate_uuid4(case_id)
    root, case_dir = _checked_storage_path(storage_root, "cases", normalized)
    if case_dir.exists():
        _reject_symlinks_in_tree(case_dir)
        shutil.rmtree(case_dir)
    _prune_empty_directories(case_dir.parent, root)


def quarantine_case_storage(storage_root: Path, case_id: str, event_id: str) -> QuarantinedCase:
    normalized_case = _validate_uuid4(case_id)
    normalized_event = _validate_uuid(event_id, "event_id")
    root, quarantine_root = _checked_storage_path(storage_root, "quarantine", normalized_event)
    moves: list[tuple[Path, Path]] = []
    try:
        for area in ("cases", "reports"):
            _, source = _checked_storage_path(root, area, normalized_case)
            if not source.exists():
                continue
            _reject_symlinks_in_tree(source)
            _, destination = _checked_storage_path(root, "quarantine", normalized_event, area)
            destination.parent.mkdir(parents=True, exist_ok=True)
            source.replace(destination)
            moves.append((source, destination))
    except BaseException:
        restore_quarantined_case(QuarantinedCase(normalized_event, tuple(moves), root))
        raise
    return QuarantinedCase(normalized_event, tuple(moves), root)


def restore_quarantined_case(quarantined: QuarantinedCase) -> None:
    for source, destination in reversed(quarantined.moves):
        if not destination.exists():
            continue
        if source.exists():
            raise FileExistsError("cannot restore quarantined case storage")
        source.parent.mkdir(parents=True, exist_ok=True)
        destination.replace(source)
        _prune_empty_directories(destination.parent, quarantined.storage_root)


def cleanup_quarantine(storage_root: Path, quarantined: QuarantinedCase) -> None:
    root, quarantine_root = _checked_storage_path(
        storage_root, "quarantine", quarantined.event_id
    )
    if quarantine_root.exists():
        _reject_symlinks_in_tree(quarantine_root)
        shutil.rmtree(quarantine_root)
    _prune_empty_directories(quarantine_root.parent, root)
