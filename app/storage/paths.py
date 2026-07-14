from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath

from app.domain.exceptions import PathTraversalError, UnsupportedFileTypeError


def safe_join(root: Path, *parts: str) -> Path:
    """Join untrusted path components beneath *root* without allowing escapes."""
    resolved_root = Path(root).resolve()
    for part in parts:
        if not isinstance(part, str) or part in ("", ".", ".."):
            raise PathTraversalError(f"非法路径片段: {part!r}")

        native_path = Path(part)
        windows_path = PureWindowsPath(part)
        if (
            native_path.is_absolute()
            or windows_path.is_absolute()
            or windows_path.drive
            or ".." in native_path.parts
            or ".." in windows_path.parts
        ):
            raise PathTraversalError(f"非法路径片段: {part!r}")

    candidate = resolved_root.joinpath(*parts).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise PathTraversalError(f"越界路径: {candidate}") from exc
    return candidate


def validate_upload_name(filename: str, allowed: frozenset[str]) -> str:
    """Return a portable basename whose extension is explicitly allowed."""
    name = os.path.basename(filename.replace("\\", "/"))
    if not name or name in {".", ".."} or "\x00" in name or ":" in name:
        raise UnsupportedFileTypeError("非法上传文件名")

    extension = os.path.splitext(name)[1].lower()
    allowed_extensions = frozenset(ext.lower() for ext in allowed)
    if extension not in allowed_extensions:
        raise UnsupportedFileTypeError(
            f"暂不支持 {extension or '未知'} 文件，仅处理文本型 DOCX"
        )
    return name
