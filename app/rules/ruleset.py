"""Load the active review ruleset and terminology from configured YAML assets.

The authoritative example ruleset and terminology live *outside* this repository
(the ``本地版示例数据包/`` bundle, marked ``DEMO_ONLY``) and are located through
the ``REVIEW_DEMO_ROOT`` environment variable or documented fallback paths.  This
module reads them through the production loaders and returns validated
``RuleDefinition``/``TerminologyMap`` objects; it never copies source documents.

It is deliberately DOCX-agnostic: callers that already hold a parsed document
(the API) reuse :func:`load_active_ruleset` without any file-validation coupling.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.domain.exceptions import RuleLoadError
from app.domain.schemas import RuleDefinition
from app.extraction.terminology import TerminologyMap
from app.rules.loader import load_rules, load_terminology
from app.rules.feature_flags import is_rule_enabled

# app/rules/ruleset.py -> parents[0]=app/rules, parents[1]=app, parents[2]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REPO_RULES_PATH = _REPO_ROOT / "app" / "rules" / "repo_rules.yaml"
_RULESET_RELATIVE = "rules/ruleset-demo-0.1.yaml"
_TERMINOLOGY_RELATIVE = "rules/terminology-demo-0.1.yaml"


class RulesetError(RuntimeError):
    """Base error for ruleset/terminology loading problems."""


class RulesetNotConfigured(RulesetError):
    """Raised when no configured or discoverable ruleset root exists."""


@dataclass(frozen=True)
class LoadedRuleset:
    """The active rule set plus terminology, without any DOCX coupling."""

    root: Path
    rules_path: Path
    terminology_path: Path
    rules: list[RuleDefinition]
    terminology: TerminologyMap


def _first_bundle_with_sentinel(starts: list[Path]) -> Path | None:
    """Return the first ``本地版示例数据包`` dir under a start path that actually
    contains the ruleset file, or None.

    Gating on the sentinel ``rules/ruleset-demo-0.1.yaml`` (not merely the folder
    name) avoids picking up an unrelated same-named directory.
    """
    seen: set[Path] = set()
    for start in starts:
        if start in seen:
            continue
        seen.add(start)
        candidate = start / "本地版示例数据包"
        if (candidate / _RULESET_RELATIVE).is_file():
            return candidate.resolve()
    return None


def resolve_ruleset_root() -> Path:
    """Resolve ``本地版示例数据包`` using only the documented locations."""
    configured = os.environ.get("REVIEW_DEMO_ROOT")
    if configured:
        candidate = Path(configured)
        if candidate.is_dir():
            return candidate.resolve()
        raise RulesetNotConfigured(f"REVIEW_DEMO_ROOT 不存在或不是目录: {candidate}")

    # Walk the ancestors of both the installed package and the working directory
    # so the bundle is found whether the app runs from the repo root or a nested
    # git worktree (where the bundle sits several levels above the repo root).
    cwd = Path.cwd()
    search_starts = [_REPO_ROOT, *_REPO_ROOT.parents, cwd, *cwd.parents]
    found = _first_bundle_with_sentinel(search_starts)
    if found is not None:
        return found
    rendered = "、".join(str(start / "本地版示例数据包") for start in (_REPO_ROOT, cwd))
    raise RulesetNotConfigured("找不到示例数据包；请设置 REVIEW_DEMO_ROOT。已检查其祖先目录，例如: " + rendered)


def _read_yaml(path: Path, label: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RulesetError(f"无法读取{label} YAML: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RulesetError(f"{label} YAML 根节点必须是对象: {path}")
    return value


def _asset_path(root: Path, relative: str, label: str) -> Path:
    path = root / relative
    if not path.is_file():
        raise RulesetError(f"缺少{label}文件: {path}")
    return path


def load_production_rules(
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
        raise RulesetError("规则 YAML 必须包含顶层 rules 列表")

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
            raise RulesetError(f"rules[{index}] 必须是对象")
        if row.get("source_type") != "DEMO_ONLY":
            raise RulesetError(f"rules[{index}] 必须明确 source_type: DEMO_ONLY")
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
            params["parameters"] = list(terminology.canonical_to_aliases)
            params["aliases_by_parameter"] = {
                canonical: [alias for alias in aliases if alias != canonical]
                for canonical, aliases in terminology.canonical_to_aliases.items()
            }
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
            raise RulesetError(f"规则校验失败: {exc}") from exc


def load_production_terminology(path: Path) -> TerminologyMap:
    document = _read_yaml(path, "术语")
    aliases = document.get("aliases")
    if aliases is None:
        raise RulesetError("术语 YAML 必须包含顶层 aliases 对象")
    with tempfile.TemporaryDirectory(prefix="review-demo-terminology-") as temp_dir:
        normalized_path = Path(temp_dir) / path.name
        normalized_path.write_text(
            yaml.safe_dump({"aliases": aliases}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        try:
            return load_terminology(normalized_path)
        except RuleLoadError as exc:
            raise RulesetError(f"术语校验失败: {exc}") from exc


def load_repo_rules(terminology: TerminologyMap) -> list[RuleDefinition]:
    """Load repo-owned generic rules and inject terminology-derived params.

    These rules are authored by this project, in addition to the bundled
    10-rule DEMO set, to express generic checks that set does not cover. The
    ``prose_alias_unnormalized`` rule receives its alias vocabulary from the
    loaded terminology map — the same explicit config-injection pattern used for
    ``alias_normalization`` — so no alias names are hardcoded in this module.
    """
    try:
        rules = load_rules(_REPO_RULES_PATH)
    except RuleLoadError as exc:
        raise RulesetError(f"仓库规则校验失败: {exc}") from exc
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
    return [
        rule.model_copy(update={"enabled": is_rule_enabled(rule.rule_id, rule.enabled)})
        for rule in injected
    ]


def load_active_ruleset(root: Path | None = None) -> LoadedRuleset:
    """Load the active authoritative + repo-owned rules and terminology.

    ``root`` defaults to :func:`resolve_ruleset_root`.  Raises
    :class:`RulesetNotConfigured` when no ruleset location is configured, and
    :class:`RulesetError` when a located asset is missing or invalid.
    """
    resolved = root if root is not None else resolve_ruleset_root()
    rules_path = _asset_path(resolved, _RULESET_RELATIVE, "规则")
    terminology_path = _asset_path(resolved, _TERMINOLOGY_RELATIVE, "术语")
    terminology = load_production_terminology(terminology_path)
    rules = load_production_rules(rules_path, terminology) + load_repo_rules(terminology)
    return LoadedRuleset(
        root=resolved,
        rules_path=rules_path,
        terminology_path=terminology_path,
        rules=rules,
        terminology=terminology,
    )
