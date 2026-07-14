"""Load the checked-in demo rules without copying source documents.

The demo package is deliberately kept outside this repository.  This module only
reads its YAML files and records the caller-provided DOCX path; it never writes a
source document to ``storage/`` or any other project directory.
"""

import argparse
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class DemoImportError(RuntimeError):
    """Raised when a demo asset cannot be found or is not supported."""


class DemoRootNotFound(DemoImportError):
    """Raised when the explicitly configured or allowed demo root is absent."""


@dataclass(frozen=True)
class DemoImport:
    """References and parsed metadata for one externally supplied demo DOCX."""

    demo_root: Path
    source_docx: Path
    rules_path: Path
    terminology_path: Path
    rules: dict[str, Any]
    terminology: dict[str, Any]


def resolve_demo_root() -> Path:
    """Resolve ``本地版示例数据包`` using only the documented locations."""
    configured = os.environ.get("REVIEW_DEMO_ROOT")
    if configured:
        candidate = Path(configured)
        if candidate.is_dir():
            return candidate.resolve()
        raise DemoRootNotFound(
            f"REVIEW_DEMO_ROOT 不存在或不是目录: {candidate}"
        )

    candidates = (
        Path(__file__).parents[3] / "本地版示例数据包",
        Path.cwd().parent / "本地版示例数据包",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    rendered = "、".join(str(path) for path in candidates)
    raise DemoRootNotFound(
        "找不到示例数据包；请设置 REVIEW_DEMO_ROOT。已检查: " + rendered
    )


def validate_docx(source: Path) -> Path:
    """Validate an externally supplied, existing DOCX path without opening/copying it."""
    source = Path(source)
    if not source.exists():
        raise DemoImportError(f"DOCX 文件不存在: {source}")
    if not source.is_file():
        raise DemoImportError(f"DOCX 路径不是文件: {source}")
    if source.suffix.casefold() != ".docx":
        raise DemoImportError(f"仅支持 DOCX 文件（当前不是 DOCX）: {source}")
    return source.resolve()


def _read_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DemoImportError(f"无法读取{label} YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoImportError(f"{label} YAML 根节点必须是对象: {path}")
    metadata = value.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("source_type") != "DEMO_ONLY":
        raise DemoImportError(f"{label} 必须明确标记 source_type: DEMO_ONLY: {path}")
    return value


def _asset_path(root: Path, relative: str, label: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise DemoImportError(f"缺少{label}文件: {path}")
    return path


def import_demo(source_docx: Path, *, storage_root: Path | None = None) -> DemoImport:
    """Read demo rules/terminology and retain only an external DOCX reference.

    ``storage_root`` is accepted for callers that already have a storage setting,
    but is intentionally unused: importing a demo must not copy source files into
    application storage.
    """
    del storage_root
    source = validate_docx(source_docx)
    root = resolve_demo_root()
    rules_path = _asset_path(root, "rules/ruleset-demo-0.1.yaml", "规则")
    terminology_path = _asset_path(root, "rules/terminology-demo-0.1.yaml", "术语")
    return DemoImport(
        demo_root=root,
        source_docx=source,
        rules_path=rules_path,
        terminology_path=terminology_path,
        rules=_read_yaml(rules_path, "规则"),
        terminology=_read_yaml(terminology_path, "术语"),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入 DEMO_ONLY 规则并引用外部 DOCX")
    parser.add_argument("docx", type=Path, help="外部示例 DOCX 路径（不会复制到 storage/）")
    args = parser.parse_args(argv)
    try:
        result = import_demo(args.docx)
    except DemoImportError as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "demo_root": str(result.demo_root),
                "source_docx": str(result.source_docx),
                "rules": str(result.rules_path),
                "terminology": str(result.terminology_path),
                "source_type": "DEMO_ONLY",
                "copied_to_storage": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI wrapper
    raise SystemExit(main())
