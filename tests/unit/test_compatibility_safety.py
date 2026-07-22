"""Guards that the fake-green prose-grep compatibility layer stays removed.

Task 23's earlier baseline reached green by grepping DEMO document prose for
verdict trigger strings (e.g. 建设周期冲突) and by injecting synthetic rules via
rule-ID branches. These tests fail if any of that special-casing returns, and
assert the honest replacement operators do not false-positive.
"""

from __future__ import annotations

from pathlib import Path

from app.domain.enums import BlockType, RuleStatus
from app.domain.schemas import ParameterFact, SourceSpan
from app.rules.operators import OperatorContext, get_operator

ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_SUBSTRINGS = (
    "COMPAT_OPERATOR_NAMES",
    "compatibility_profile",
    "demo_compatibility",
    "legacy_compatibility",
    "建设周期冲突",
    "投产时间与建设周期",
    "同义参数表达不统一",
)
LEGACY_BUSINESS_MARKERS = (
    "legacy_compatibility",
    "compatibility_profile",
    "demo_compatibility",
)
SCAN_DIRS = (ROOT / "app", ROOT / "scripts")


def _source_files() -> list[Path]:
    files: list[Path] = []
    for base in SCAN_DIRS:
        files.extend(path for path in base.rglob("*.py") if "__pycache__" not in path.parts)
    return files


def test_no_prose_grep_or_legacy_special_casing_remains() -> None:
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN_SUBSTRINGS:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {needle!r}")
        for needle in LEGACY_BUSINESS_MARKERS:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)} contains {needle!r}")
    assert not offenders, offenders


def test_demo_compatibility_yaml_is_deleted() -> None:
    assert not (ROOT / "app" / "rules" / "demo_compatibility.yaml").exists()


def test_no_rule_id_branches_in_importer_or_engine() -> None:
    for relative in ("scripts/import_demo.py", "app/rules/engine.py"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert 'rule_id") ==' not in text, relative
        assert "rule_id ==" not in text, relative


def _paragraph(text: str, sid: str, document_id: str = "D") -> SourceSpan:
    return SourceSpan(
        span_id=sid,
        document_id=document_id,
        section_path=["开发部署方案"],
        block_type=BlockType.PARAGRAPH,
        paragraph_index=1,
        text=text,
        text_hash="h",
    )


def test_reply_operator_stays_silent_without_a_reply_table() -> None:
    outcome = get_operator("reply_table_status_complete")(
        OperatorContext([], [_paragraph("正文没有回复表。", "p1")]),
        {
            "section_contains": "审查意见回复表",
            "id_header_terms": ["意见编号"],
            "status_header_terms": ["回复", "状态"],
        },
    )
    assert outcome.status is RuleStatus.UNKNOWN


def test_alias_operator_ignores_generic_substring_alias() -> None:
    outcome = get_operator("prose_alias_unnormalized")(
        OperatorContext([], [_paragraph("其中生产井32口。", "p1")]),
        {"terms": [{"canonical": "生产井数", "aliases": ["生产井"]}]},
    )
    assert outcome.status is RuleStatus.PASS
