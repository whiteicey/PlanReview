"""External DEMO golden-test adapter.

The DEMO package is deliberately outside the repository.  This adapter reads it
only when REVIEW_DEMO_ROOT explicitly names a package that contains the mapped
DOCX and production rule assets; it never creates stand-in DOCX files or
silently turns a missing package into a passing test.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from app.domain.enums import BlockType, OnMissing, Severity
from app.domain.schemas import RuleDefinition, SourceSpan
from app.llm.mock import MockProvider
from app.parsers.docx_parser import DocxParser, ParsedDocument
from app.review.pipeline import ReviewPipeline, ReviewRun
from app.storage.hashing import sha256_text
from scripts.import_demo import DemoImportError, import_demo

TESTS_ROOT = Path(__file__).resolve().parent
EXPECTED_CASES_PATH = TESTS_ROOT / "golden_cases_demo.expected.jsonl"

# Prefer an explicit REVIEW_DEMO_ROOT override; otherwise fall back to the sample
# package shipped in-repo (resolved via the same ancestor-walk the app uses).
# When neither is available the golden cases skip honestly rather than fake a pass.
def _discover_demo_root() -> Path | None:
    override = os.environ.get("REVIEW_DEMO_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    try:
        from app.rules.ruleset import RulesetNotConfigured, resolve_ruleset_root

        return resolve_ruleset_root()
    except (RulesetNotConfigured, Exception):
        return None


DEMO_ROOT = _discover_demo_root()

DEMO_FILES = {
    "DEMO-001": "DEMO-001_正常基线方案_V1.0.docx",
    "DEMO-002": "DEMO-002_综合参数冲突方案_V1.0.docx",
    "DEMO-003-V1": "DEMO-003_版本变化_V1.0.docx",
    "DEMO-003-V2": "DEMO-003_版本变化_V2.0.docx",
    "DEMO-004": "DEMO-004_综合缺陷方案_V1.0.docx",
}


def _load_cases() -> dict[str, dict[str, Any]]:
    return {
        row["case_id"]: row
        for row in (
            json.loads(line)
            for line in EXPECTED_CASES_PATH.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    }


def _external_paths() -> dict[str, Path]:
    if DEMO_ROOT is None or not DEMO_ROOT.is_dir():
        pytest.skip("外部 DEMO 数据未提供")
    paths = {case: DEMO_ROOT / "plans" / name for case, name in DEMO_FILES.items()}
    required = [
        *paths.values(),
        DEMO_ROOT / "rules" / "ruleset-demo-0.1.yaml",
        DEMO_ROOT / "rules" / "terminology-demo-0.1.yaml",
    ]
    if not all(path.is_file() for path in required):
        pytest.skip("外部 DEMO 数据未提供")
    return paths


def _span(
    document_id: str,
    text: str,
    table_index: int,
    row_index: int,
    column_index: int,
) -> SourceSpan:
    return SourceSpan(
        span_id=f"{document_id}:t:{table_index}:{row_index}:{column_index}",
        document_id=document_id,
        block_type=BlockType.TABLE_CELL,
        table_index=table_index,
        row_index=row_index,
        column_index=column_index,
        text=text,
        text_hash=sha256_text(text),
    )


def _parameter_table(document_id: str, rows: list[list[str]]) -> ParsedDocument:
    headers = ["参数名称", "数值", "单位", "对象", "时间/阶段", "统计口径", "条件"]
    values = [headers, *rows]
    cells = [
        _span(document_id, text, 0, row_index, column_index)
        for row_index, row in enumerate(values)
        for column_index, text in enumerate(row)
    ]
    return ParsedDocument(
        document_id=document_id,
        file_name=f"{document_id}.docx",
        spans=cells,
        paragraphs=[],
        table_cells=cells,
    )


def _rule(
    rule_id: str,
    operator: str,
    params: dict[str, Any],
    *,
    on_missing: OnMissing = OnMissing.UNKNOWN,
    category: str = "consistency",
) -> RuleDefinition:
    return RuleDefinition(
        rule_id=rule_id,
        version="golden",
        name=rule_id,
        category=category,
        severity=Severity.MEDIUM,
        operator=operator,
        on_missing=on_missing,
        params=params,
    )


def _synthetic_case(case_id: str) -> tuple[list[ParsedDocument], list[RuleDefinition]]:
    common = ["气田_A", "达产期", "设计工况", ""]
    if case_id == "G-005":
        return (
            [_parameter_table("G-005", [["开发井总数", "36", "口", "气田_A", "建设期", "规划部署", ""], ["开发井总数", "38", "口", "气田_A", "达产期", "规划部署", ""]])],
            [_rule("CONSISTENCY-001", "all_equal", {"parameter": "开发井总数"})],
        )
    if case_id == "G-006":
        return (
            [_parameter_table("G-006", [["开发井总数", "36", "口", "气田_A", "建设期", "规划部署", ""], ["开发井总数", "38", "口", "气田_A", "建设期", "日峰值", ""]])],
            [_rule("CONSISTENCY-001", "all_equal", {"parameter": "开发井总数"})],
        )
    if case_id == "G-007":
        return (
            [_parameter_table("G-007", [["开发井总数", "2", "口", *common], ["单井设计产能", "5", "万m³/d", *common], ["总设计产能", "100000", "m³/d", *common]])],
            [_rule("CONSISTENCY-003", "product_approximately_equals", {"left": ["开发井总数", "单井设计产能"], "right": "总设计产能"}, category="aggregation")],
        )
    if case_id == "G-008":
        return (
            [ParsedDocument("G-008", "G-008.docx", [], [], [])],
            [_rule("EVIDENCE-001", "evidence_required", {"min_evidence": 1}, on_missing=OnMissing.BLOCK, category="evidence")],
        )
    raise KeyError(case_id)


@pytest.fixture(scope="session")
def expected_cases() -> dict[str, dict[str, Any]]:
    return _load_cases()


@pytest.fixture
def run_golden_case(expected_cases: dict[str, dict[str, Any]]) -> Callable[[str], ReviewRun]:
    """Run a named golden case through the production parser/pipeline/rules."""

    def run(case_id: str) -> ReviewRun:
        if case_id not in expected_cases:
            raise KeyError(f"unknown golden case: {case_id}")
        if case_id.startswith("G-00") and case_id not in {"G-005", "G-006", "G-007", "G-008"}:
            paths = _external_paths()
            document_keys = expected_cases[case_id]["documents"]
            document_paths = [paths[key] for key in document_keys]
            try:
                imported = import_demo(document_paths[0])
            except DemoImportError as exc:
                pytest.skip(f"外部 DEMO 数据未提供: {exc}")
            documents = [
                DocxParser().parse(path, document_id=key)
                for key, path in zip(document_keys, document_paths, strict=True)
            ]
            run = ReviewPipeline(imported.terminology).run(
                case_id, documents, imported.rules, MockProvider()
            )
            return run

        documents, rules = _synthetic_case(case_id)
        return ReviewPipeline().run(case_id, documents, rules, MockProvider())

    return run
