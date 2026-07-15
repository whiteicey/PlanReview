"""Load DEMO_ONLY rules without copying source documents.

The demo package is deliberately kept outside this repository. This module reads
its YAML files through the production loaders and records the caller-provided
DOCX path; it never writes a source document to ``storage/``.
"""

import argparse
import json
import os
import sys
import tempfile

from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Make ``python scripts/import_demo.py`` behave like a project-root invocation.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import yaml

from app.domain.exceptions import RuleLoadError
from app.domain.schemas import RuleDefinition
from app.extraction.terminology import TerminologyMap
from app.rules.loader import load_rules, load_terminology


class DemoImportError(RuntimeError):
    """Raised when a demo asset cannot be found or is not supported."""


class DemoRootNotFound(DemoImportError):
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
    """Resolve ``本地版示例数据包`` using only the documented locations."""
    configured = os.environ.get("REVIEW_DEMO_ROOT")
    if configured:
        candidate = Path(configured)
        if candidate.is_dir():
            return candidate.resolve()
        raise DemoRootNotFound(f"REVIEW_DEMO_ROOT 不存在或不是目录: {candidate}")

    candidates = (
        Path(__file__).parents[3] / "本地版示例数据包",
        Path.cwd().parent / "本地版示例数据包",
    )
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    rendered = "、".join(str(path) for path in candidates)
    raise DemoRootNotFound("找不到示例数据包；请设置 REVIEW_DEMO_ROOT。已检查: " + rendered)


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


def _read_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DemoImportError(f"无法读取{label} YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoImportError(f"{label} YAML 根节点必须是对象: {path}")
    return value


def _asset_path(root: Path, relative: str, label: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise DemoImportError(f"缺少{label}文件: {path}")
    return path


def _load_production_rules(
    path: Path, terminology: TerminologyMap | None = None
) -> list[RuleDefinition]:
    """Normalize the legacy DEMO vocabulary into production rule params.

    The external bundle predates ``RuleDefinition.params`` and stores operator
    arguments beside rule metadata.  This translation is explicit and
    fail-closed; it does not make unknown scopes comparable or add inferred
    values.
    """
    document = _read_yaml(path, "规则")
    rows = document.get("rules")
    if not isinstance(rows, list):
        raise DemoImportError("规则 YAML 必须包含顶层 rules 列表")

    normalized_rows: list[dict[str, Any]] = []
    known_fields = set(RuleDefinition.model_fields)
    operator_argument_keys = {
        "parameter", "parameters", "selectors", "required_sections",
        "required_table_keywords", "target", "components", "left", "right",
        "relative_tolerance", "match_dimensions", "reason_keywords",
        "required_status", "minimum_evidence_count", "dictionary",
    }
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DemoImportError(f"rules[{index}] 必须是对象")
        if row.get("source_type") != "DEMO_ONLY":
            raise DemoImportError(f"rules[{index}] 必须明确 source_type: DEMO_ONLY")
        normalized = {key: value for key, value in row.items() if key in known_fields}
        params = dict(row.get("params") or {})
        params.update({key: row[key] for key in operator_argument_keys if key in row})
        if "required_table_keywords" in params:
            keywords = params.pop("required_table_keywords")
            params["section_contains"] = keywords[0] if isinstance(keywords, list) and keywords else None
        if "reason_keywords" in params:
            params["reason_terms"] = params.pop("reason_keywords")
        if "required_status" in params:
            params["status_terms"] = params.pop("required_status")
        if "minimum_evidence_count" in params:
            params["min_evidence"] = params.pop("minimum_evidence_count")
        # Map the operator's target parameter onto ``parameter`` so RuleResult
        # can label the finding. Aggregation/capacity operators read their own
        # target/left/right keys; this only sets the display parameter.
        if params.get("target"):
            params.setdefault("parameter", params["target"])
        elif isinstance(params.get("left"), str) and params.get("right"):
            params.setdefault("parameter", params["left"])
        elif isinstance(params.get("left"), list) and params.get("right"):
            params.setdefault("parameter", params["right"])
        if "match_dimensions" in params:
            # The authoritative rules name a subset of scope dimensions; the
            # strict operator contract requires all five, so an explicit partial
            # list is dropped rather than silently widening the comparison.
            params.pop("match_dimensions")
        if normalized.get("operator") == "alias_normalization" and terminology is not None:
            canonical, aliases = next(iter(terminology.canonical_to_aliases.items()), (None, ()))
            if canonical:
                params["canonical_name"] = canonical
                params["aliases"] = [alias for alias in aliases if alias != canonical]
        if normalized.get("operator") == "issue_response_status_exists":
            # The status-existence operator reads the reply table structurally,
            # like the reply-completeness rule; supply the same header contract.
            params.setdefault("section_contains", "审查意见回复表")
            params.setdefault("id_header_terms", ["意见编号", "意见"])
            params.setdefault("status_header_terms", ["回复", "状态"])
        # Older generated demo bundles used ``suspected`` for missing data,
        # while the production enum deliberately uses UNKNOWN. Preserve intent
        # as metadata, but validate through the production three-value model.
        # A ``suspected`` policy also means the external contract wants human
        # escalation, expressed declaratively rather than by a rule-ID branch.
        if normalized.get("on_missing") == "suspected":
            params["demo_on_missing"] = "suspected"
            normalized["on_missing"] = "unknown"
            normalized["requires_human_review"] = True
        normalized["params"] = params
        normalized_rows.append(normalized)

    # The production loader intentionally validates the established top-level
    # ``rules`` schema. Metadata is package-level compatibility data, so it is
    # removed before handing the normalized document to that loader.
    with tempfile.TemporaryDirectory(prefix="review-demo-rules-") as temp_dir:
        normalized_path = Path(temp_dir) / path.name
        normalized_path.write_text(
            yaml.safe_dump({"rules": normalized_rows}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        try:
            return load_rules(normalized_path)
        except RuleLoadError as exc:
            raise DemoImportError(f"规则校验失败: {exc}") from exc


def _load_production_terminology(path: Path) -> TerminologyMap:
    document = _read_yaml(path, "术语")
    aliases = document.get("aliases")
    if aliases is None:
        raise DemoImportError("术语 YAML 必须包含顶层 aliases 对象")
    with tempfile.TemporaryDirectory(prefix="review-demo-terminology-") as temp_dir:
        normalized_path = Path(temp_dir) / path.name
        normalized_path.write_text(
            yaml.safe_dump({"aliases": aliases}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        try:
            return load_terminology(normalized_path)
        except RuleLoadError as exc:
            raise DemoImportError(f"术语校验失败: {exc}") from exc


def _load_repo_rules(terminology: TerminologyMap) -> list[RuleDefinition]:
    """Load repo-owned generic rules and inject terminology-derived params.

    These rules are authored by this project (not the external authoritative
    bundle) to express generic checks the 10-rule DEMO set does not cover. The
    ``prose_alias_unnormalized`` rule receives its alias vocabulary from the
    loaded terminology map — the same explicit config-injection pattern used for
    ``alias_normalization`` — so no alias names are hardcoded in this module.
    """
    repo_path = Path(__file__).resolve().parents[1] / "app" / "rules" / "repo_rules.yaml"
    try:
        rules = load_rules(repo_path)
    except RuleLoadError as exc:
        raise DemoImportError(f"仓库规则校验失败: {exc}") from exc
    terms = [
        {
            "canonical": canonical,
            "aliases": sorted(alias for alias in aliases if alias != canonical),
        }
        for canonical, aliases in terminology.canonical_to_aliases.items()
    ]
    injected: list[RuleDefinition] = []
    for rule in rules:
        if rule.operator == "prose_alias_unnormalized":
            injected.append(rule.model_copy(update={"params": {**rule.params, "terms": terms}}))
        else:
            injected.append(rule)
    return injected


def import_demo(source_docx: Path, *, storage_root: Path | None = None) -> DemoImport:
    """Load production-validated demo rules and terminology without file copying."""
    del storage_root
    source = validate_docx(source_docx)
    root = resolve_demo_root()
    rules_path = _asset_path(root, "rules/ruleset-demo-0.1.yaml", "规则")
    terminology_path = _asset_path(root, "rules/terminology-demo-0.1.yaml", "术语")
    terminology = _load_production_terminology(terminology_path)
    return DemoImport(
        demo_root=root,
        source_docx=source,
        rules_path=rules_path,
        terminology_path=terminology_path,
        rules=(
            _load_production_rules(rules_path, terminology)
            + _load_repo_rules(terminology)
        ),
        terminology=terminology,
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
