from __future__ import annotations

import hashlib

from app.domain.enums import BlockType
from app.domain.schemas import SourceSpan
from scripts.replay_evidence_selector_v12 import compare_metrics, plan_metrics
from app.review.evidence_packets import build_evidence_plan


def _span(span_id: str, text: str, *, chapter: int, block=BlockType.PARAGRAPH, table=None, row=None, col=None):
    return SourceSpan(
        span_id=span_id,
        document_id="doc",
        section_path=[f"{chapter} chapter"],
        block_type=block,
        table_index=table,
        row_index=row,
        column_index=col,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def test_relation_replay_metrics_include_back_half_and_budget_gate():
    spans = [
        _span("h11", "11 chapter", chapter=11, block=BlockType.HEADING),
        _span("p11", "demand 100 capacity 120", chapter=11),
        _span("h42", "42 appendix", chapter=42, block=BlockType.HEADING),
        _span("t0", "parameter", chapter=42, block=BlockType.TABLE_CELL, table=1, row=0, col=0),
        _span("t1", "unit", chapter=42, block=BlockType.TABLE_CELL, table=1, row=0, col=1),
        _span("r0", "capacity", chapter=42, block=BlockType.TABLE_CELL, table=1, row=1, col=0),
        _span("r1", "120", chapter=42, block=BlockType.TABLE_CELL, table=1, row=1, col=1),
    ]
    plan = build_evidence_plan(spans, [], [])
    metrics = plan_metrics(plan)
    assert metrics["back_half_chapter_count"] >= 1
    assert metrics["batch_count"] <= 6
    assert metrics["ordinary_packet_count"] > 0
    assert metrics["relation_packet_ratio"] <= 0.70
    gate = compare_metrics(metrics, metrics)
    assert gate["passed"] is True
