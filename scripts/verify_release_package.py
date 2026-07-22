"""Verify that a source tree or release ZIP is complete and contains no runtime data."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile


REQUIRED_RELEASE_FILES = frozenset(
    {
        "README.md",
        "pyproject.toml",
        "app/main.py",
        "app/api/routes.py",
        "app/api/schemas.py",
        "app/llm/adapters/anthropic.py",
        "app/llm/limits.py",
        "app/llm/provider.py",
        "app/persistence/db.py",
        "app/persistence/models.py",
        "app/persistence/repository.py",
        "app/review/background_jobs.py",
        "app/review/parsed_cache.py",
        "app/review/progress.py",
        "app/review/pipeline.py",
        "app/parsers/docx_parser.py",
        "app/storage/__init__.py",
        "app/storage/audit.py",
        "app/storage/case_files.py",
        "app/storage/hashing.py",
        "app/storage/paths.py",
        "scripts/run_local.py",
        "scripts/smoke_review_progress.py",
        "web/index.html",
        "web/app.js",
        "web/review_state.js",
        "web/review_state.test.js",
        "web/review_display_queue.js",
        "web/review_display_queue.test.js",
        "web/review_progress.js",
        "web/review_progress.test.js",
        "tests/unit/test_anthropic_adapter.py",
        "tests/unit/test_llm_limits.py",
        "tests/unit/test_review_pipeline_failure.py",
        "web/styles.css",
        "本地版示例数据包/README.md",
        "本地版示例数据包/rules/ruleset-demo-0.1.yaml",
        "本地版示例数据包/rules/terminology-demo-0.1.yaml",
        "本地版示例数据包/golden/golden_cases_demo.jsonl",
        "本地版示例数据包/plans/DEMO-001_正常基线方案_V1.0.docx",
        "本地版示例数据包/plans/DEMO-002_综合参数冲突方案_V1.0.docx",
        "本地版示例数据包/plans/DEMO-003_版本变化_V1.0.docx",
        "本地版示例数据包/plans/DEMO-003_版本变化_V2.0.docx",
        "本地版示例数据包/plans/DEMO-004_综合缺陷方案_V1.0.docx",
        "本地版示例数据包/historical_opinions/历史审查意见_示例.docx",
        "本地版示例数据包/historical_opinions/历史审查意见_示例模板.xlsx",
    }
)

_FORBIDDEN_ROOTS = frozenset({".git", ".pytest_cache", ".venv", "node_modules", "runtime", "storage"})
_FORBIDDEN_SEGMENTS = frozenset({"__pycache__"})
_FORBIDDEN_SUFFIXES = (".db", ".sqlite3", ".log", ".pyc")


class ReleasePackageError(ValueError):
    """Raised when an export is incomplete or contains forbidden runtime data."""


@dataclass(frozen=True)
class ReleasePackageResult:
    archive: bool
    member_count: int
    required_file_count: int


def _normalized_member(value: str) -> str:
    normalized = str(PurePosixPath(value.replace("\\", "/")))
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_forbidden(member: str) -> bool:
    path = PurePosixPath(member)
    parts = path.parts
    if not parts:
        return False
    lower_parts = tuple(part.casefold() for part in parts)
    name = lower_parts[-1]
    if lower_parts[0] in _FORBIDDEN_ROOTS:
        return True
    if any(part in _FORBIDDEN_SEGMENTS for part in lower_parts):
        return True
    if name == ".env" or name.startswith(".env.") or name == "llm_config.json":
        return True
    if name.endswith(_FORBIDDEN_SUFFIXES):
        return True
    return len(lower_parts) >= 2 and lower_parts[0] == "configs" and name.startswith("secrets")


def _tracked_members(root: Path) -> set[str]:
    git_dir = root / ".git"
    if git_dir.exists():
        completed = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        return {
            _normalized_member(item.decode("utf-8"))
            for item in completed.stdout.split(b"\0")
            if item
        }
    # A verified extraction has no .git metadata and may already have been
    # installed or tested. Ignore artifacts generated locally in directory
    # mode; archive mode remains strict and rejects the same members.
    members = {
        _normalized_member(path.relative_to(root).as_posix())
        for path in root.rglob("*")
        if path.is_file()
    }
    return {member for member in members if not _is_forbidden(member)}


def _zip_members(path: Path) -> set[str]:
    try:
        with ZipFile(path) as archive:
            return {
                _normalized_member(info.filename)
                for info in archive.infolist()
                if not info.is_dir()
            }
    except (OSError, BadZipFile) as exc:
        raise ReleasePackageError("release archive is not a readable ZIP") from exc


def verify_release_package(path: Path | str) -> ReleasePackageResult:
    target = Path(path)
    if target.is_dir():
        members = _tracked_members(target)
        archive = False
    elif target.is_file() and target.suffix.casefold() == ".zip":
        members = _zip_members(target)
        archive = True
    else:
        raise ReleasePackageError("release target must be a directory or ZIP")

    missing = sorted(REQUIRED_RELEASE_FILES.difference(members))
    if missing:
        raise ReleasePackageError("missing required release files: " + ", ".join(missing))
    forbidden = sorted(member for member in members if _is_forbidden(member))
    if forbidden:
        raise ReleasePackageError("forbidden release files: " + ", ".join(forbidden))
    return ReleasePackageResult(
        archive=archive,
        member_count=len(members),
        required_file_count=len(REQUIRED_RELEASE_FILES),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    try:
        result = verify_release_package(args.path)
    except ReleasePackageError as exc:
        parser.error(str(exc))
    print(json.dumps(result.__dict__, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
