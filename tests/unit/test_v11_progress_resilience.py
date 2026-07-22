from __future__ import annotations

import pytest

from app.domain.enums import BlockType, PipelineStage
from app.domain.schemas import SourceSpan
from app.llm.mock import MockProvider
from app.parsers.docx_parser import ParsedDocument
from app.review.pipeline import ReviewPipeline, format_ai_batch_progress
from app.review.progress import safe_progress_payload


def _safe_message(message: str) -> str:
    return safe_progress_payload(
        "AI_REVIEW", "AI_BATCH_COMPLETED", "completed", message
    )[3]


def test_batch_fraction_is_not_mistaken_for_path() -> None:
    assert _safe_message("AI批次 1/17 已处理") == "AI批次 1/17 已处理"


def test_batch_progress_format_contains_no_path_like_slash() -> None:
    message = format_ai_batch_progress(1, 17)
    assert message == "已完成第1批AI审查，共17批"
    assert "/" not in message


def test_rule_fraction_is_not_mistaken_for_path() -> None:
    assert _safe_message("执行规则 12/12") == "执行规则 12/12"


def test_span_fraction_is_not_mistaken_for_path() -> None:
    assert _safe_message("已完成40/1719个片段") == "已完成40/1719个片段"


def test_sanitizer_path_placeholder_is_accepted() -> None:
    sanitized = _safe_message(r"读取 C:\private\report.docx 失败")
    assert "[path]" in sanitized


@pytest.fixture(scope="module")
def run_with_broken_progress_callback():
    heading = SourceSpan(
        span_id="D:p:0",
        document_id="D",
        block_type=BlockType.HEADING,
        text="1 总论",
        text_hash="heading-hash",
        section_path=["1 总论"],
    )
    paragraph = SourceSpan(
        span_id="D:p:1",
        document_id="D",
        block_type=BlockType.PARAGRAPH,
        text="本项目设计处理能力为50万m³/d。",
        text_hash="paragraph-hash",
        section_path=["1 总论"],
    )
    document = ParsedDocument(
        document_id="D",
        file_name="case.docx",
        spans=[heading, paragraph],
        paragraphs=[heading, paragraph],
        table_cells=[],
    )

    def broken_progress(*_args, **_kwargs):
        raise ValueError("simulated progress persistence failure")

    return ReviewPipeline().run(
        "progress-resilience-case",
        [document],
        [],
        MockProvider(),
        progress=broken_progress,
    )


def test_progress_write_failure_does_not_interrupt_business_task(
    run_with_broken_progress_callback,
) -> None:
    assert run_with_broken_progress_callback.final_status == "READY_FOR_HUMAN_REVIEW"


def test_provider_success_never_persists_not_run_status(
    run_with_broken_progress_callback,
) -> None:
    assert run_with_broken_progress_callback.llm_status.value != "NOT_RUN"


def test_ai_stage_does_not_remain_running_after_progress_failure(
    run_with_broken_progress_callback,
) -> None:
    llm_stage = next(
        record
        for record in run_with_broken_progress_callback.stage_records
        if record.stage == PipelineStage.LLM_REVIEWED
    )
    assert llm_stage.status == "completed"
