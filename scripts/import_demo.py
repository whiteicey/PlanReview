"""CLI wrapper: load DEMO_ONLY rules and reference an external DOCX.

The rule/terminology loading logic lives in :mod:`app.rules.ruleset` so the API
can reuse it.  This script adds the DOCX validation and command-line surface, and
preserves the historical public names (``import_demo``, ``resolve_demo_root``,
``validate_docx``, ``DemoImport``, ``DemoImportError``, ``DemoRootNotFound``).
"""

import argparse
import json
import sys

from dataclasses import dataclass
from pathlib import Path

# Make ``python scripts/import_demo.py`` behave like a project-root invocation.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.domain.schemas import RuleDefinition
from app.extraction.terminology import TerminologyMap
from app.rules.ruleset import (
    RulesetError,
    RulesetNotConfigured,
    load_active_ruleset,
    resolve_ruleset_root,
)


class DemoImportError(RulesetError):
    """Raised when a demo asset cannot be found or is not supported."""


class DemoRootNotFound(DemoImportError, RulesetNotConfigured):
    """Raised when the explicitly configured or allowed demo root is absent."""


@dataclass(frozen=True)
class DemoImport:
    """References and validated metadata for one externally supplied DOCX."""

    demo_root: Path
    source_docx: Path
    rules_path: Path
    terminology_path: Path
    rules: list[RuleDefinition]
    terminology: TerminologyMap


def resolve_demo_root() -> Path:
    """Resolve ``本地版示例数据包``; raises ``DemoRootNotFound`` when absent."""
    try:
        return resolve_ruleset_root()
    except RulesetNotConfigured as exc:
        raise DemoRootNotFound(str(exc)) from exc


def validate_docx(source: Path) -> Path:
    """Validate an existing, externally supplied DOCX without copying it."""
    source = Path(source)
    if not source.exists():
        raise DemoImportError(f"DOCX 文件不存在: {source}")
    if not source.is_file():
        raise DemoImportError(f"DOCX 路径不是文件: {source}")
    if source.suffix.casefold() != ".docx":
        raise DemoImportError(f"仅支持 DOCX 文件（当前不是 DOCX）: {source}")
    return source.resolve()


def import_demo(source_docx: Path, *, storage_root: Path | None = None) -> DemoImport:
    """Load production-validated demo rules and terminology without file copying."""
    del storage_root
    source = validate_docx(source_docx)
    try:
        root = resolve_ruleset_root()
    except RulesetNotConfigured as exc:
        raise DemoRootNotFound(str(exc)) from exc
    try:
        loaded = load_active_ruleset(root)
    except RulesetError as exc:
        raise DemoImportError(str(exc)) from exc
    return DemoImport(
        demo_root=loaded.root,
        source_docx=source,
        rules_path=loaded.rules_path,
        terminology_path=loaded.terminology_path,
        rules=loaded.rules,
        terminology=loaded.terminology,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入 DEMO_ONLY 规则并引用外部 DOCX")
    parser.add_argument("docx", type=Path, help="外部示例 DOCX 路径（不会复制到 storage/）")
    args = parser.parse_args(argv)
    try:
        result = import_demo(args.docx)
    except DemoImportError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "demo_root": str(result.demo_root),
        "source_docx": str(result.source_docx),
        "rules": str(result.rules_path),
        "terminology": str(result.terminology_path),
        "rule_count": len(result.rules),
        "source_type": "DEMO_ONLY",
        "copied_to_storage": False,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
