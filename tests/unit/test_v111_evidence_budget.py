from __future__ import annotations

import hashlib

from app.domain.enums import BlockType
from app.domain.schemas import SourceSpan
from app.review.evidence_packets import (
    MAX_AI_BATCHES,
    TARGET_BATCH_CHARACTERS_MAX,
    TARGET_PACKET_COUNT_MAX,
    TARGET_PACKET_COUNT_MIN,
    build_evidence_plan,
)


def _span(sid: str, text: str, chapter: int, table: int, row: int, col: int) -> SourceSpan:
    return SourceSpan(
        span_id=sid,
        document_id="budget-doc",
        section_path=[f"{chapter} 测试章节"],
        block_type=BlockType.TABLE_CELL,
        table_index=table,
        row_index=row,
        column_index=col,
        text=text,
        text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def _large_document() -> list[SourceSpan]:
    topic_text = (
        "章节引用及完整性",
        "处理能力设计压力规模",
        "设备数量1用1备",
        "管线DN300长度工程量",
        "供配电消防数智化报警",
        "安全环保职业卫生风险措施",
        "工期进度投资4200万元",
        "推荐方案比选结论",
    )
    spans: list[SourceSpan] = []
    # 504 independent relational rows plus their cells emulate the observed
    # 502-packet baseline without depending on any test manifest.
    for index in range(504):
        topic_index = index % len(topic_text)
        chapter = 1 + (index % 42)
        table = index // 12
        long_context = (f"{topic_text[topic_index]}，用于核对正文、表格及跨章节关系。" * 4)
        spans.extend(
            [
                _span(f"s{index}:0", f"对象{index}{topic_text[topic_index]}", chapter, table, index, 0),
                _span(f"s{index}:1", f"{index + 10} 单位", chapter, table, index, 1),
                _span(f"s{index}:2", long_context, chapter, table, index, 2),
            ]
        )
    return spans


def test_large_candidate_pool_is_compressed_by_topic_and_chapter_quota() -> None:
    plan = build_evidence_plan(_large_document(), [], [])

    diagnostics = plan.selection_diagnostics
    assert diagnostics["raw_packet_count"] >= 502
    assert TARGET_PACKET_COUNT_MIN <= len(plan.packets) <= TARGET_PACKET_COUNT_MAX
    assert 4 <= len(plan.batches) <= MAX_AI_BATCHES
    assert all(batch.estimated_characters <= TARGET_BATCH_CHARACTERS_MAX for batch in plan.batches)
    assert 50_000 <= sum(batch.estimated_characters for batch in plan.batches) <= 80_000
    assert 300 <= sum(len(batch.source_span_ids) for batch in plan.batches) <= 600
    assert any(chapter >= 30 for chapter in diagnostics["covered_chapters"])
    assert diagnostics["removal_reasons"]["budget_and_quota"] > 0


def test_atomic_table_relation_is_not_split_across_batches() -> None:
    spans = _large_document()
    plan = build_evidence_plan(spans, [], [])
    packet = next(packet for packet in plan.packets if packet.primary_span == "s0:1")
    batch = next(batch for batch in plan.batches if packet.packet_id in batch.packet_ids)

    assert set(packet.source_span_ids).issubset(batch.source_span_ids)


def test_cover_noise_and_repeated_headers_do_not_dominate_budget() -> None:
    spans = _large_document()
    for index in range(100):
        spans.append(SourceSpan(
            span_id=f"cover:{index}",
            document_id="budget-doc",
            section_path=[],
            block_type=BlockType.PARAGRAPH,
            text=f"封面说明性元数据{index}",
            text_hash=hashlib.sha256(str(index).encode()).hexdigest(),
        ))
    plan = build_evidence_plan(spans, [], [])

    assert not any(span_id.startswith("cover:") for span_id in plan.selected_span_ids)
    assert len(plan.selected_span_ids) >= len(plan.packets)
