from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_APP_ROOT = Path(__file__).resolve().parent.parent  # review/


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    app_root: Path = _APP_ROOT
    storage_root: Path = _APP_ROOT / "storage"
    runtime_root: Path = _APP_ROOT / "runtime"
    db_path: Path = _APP_ROOT / "storage" / "review.db"
    max_upload_bytes: int = 100 * 1024 * 1024
    max_zip_members: int = 5_000
    max_zip_uncompressed_bytes: int = 300 * 1024 * 1024
    max_zip_member_bytes: int = 50 * 1024 * 1024
    max_zip_compression_ratio: float = 100.0
    max_document_characters: int = 5_000_000
    max_paragraphs: int = 50_000
    max_tables: int = 2_000
    max_table_cells: int = 200_000
    allowed_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".docx"})
    )
    disclaimer: str = "AI 初审结果，不是正式审查结论"


@lru_cache
def get_settings() -> Settings:
    root_override = os.environ.get("REVIEW_STORAGE_ROOT")
    if root_override:
        root = Path(root_override).resolve()
        return Settings(
            storage_root=root,
            runtime_root=root.parent / "runtime",
            db_path=root / "review.db",
        )
    return Settings()
