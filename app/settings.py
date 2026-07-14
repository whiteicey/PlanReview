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
    db_path: Path = _APP_ROOT / "storage" / "review.db"
    max_file_bytes: int = 100 * 1024 * 1024
    max_pages: int = 300
    allowed_extensions: frozenset[str] = field(
        default_factory=lambda: frozenset({".docx"})
    )
    disclaimer: str = "AI 初审结果，不是正式审查结论"


@lru_cache
def get_settings() -> Settings:
    root_override = os.environ.get("REVIEW_STORAGE_ROOT")
    if root_override:
        root = Path(root_override).resolve()
        return Settings(storage_root=root, db_path=root / "review.db")
    return Settings()
