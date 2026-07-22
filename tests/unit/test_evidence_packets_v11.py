import hashlib
from pathlib import Path

from app.domain.enums import BlockType, FindingCategory, Origin, Severity
from app.domain.schemas import Finding, SourceSpan
from app.review.evidence_packets import build_evidence_plan, build_evidence_packets
from app.review.finding_guards import deduplicate_findings, filter_unsupported_ai_findings


def span(sid, text, *, block=BlockType.PARAGRAPH, section=None, table=None, row=None, col=None, paragraph=None):
    return SourceSpan(
        span_id=sid, document_id="doc", section_path=section or [], block_type=block,
        paragraph_index=paragraph, table_index=table, row_index=row, column_index=col,
        text=text, text_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


def finding(fid, title, evidence, *, origin=Origin.LLM, category=FindingCategory.CONSISTENCY, parameter=None):
    return Finding(
        finding_id=fid, origin=origin, category=category, severity=Severity.MEDIUM,
        parameter=parameter, title=title, description=title, suggestion="复核",
        rule_id="R-1" if origin is Origin.RULE else None,
        evidence_span_ids=evidence, needs_human_review=True,
    )


def test_existing_chapters_are_not_declared_missing_from_a_partial_batch():
    spans = [
        span("h1", "1 范围", block=BlockType.HEADING, section=["1 范围"]),
        span("p1", "本章说明范围。", section=["1 范围"]),
        span("h2", "2 基本规定", block=BlockType.HEADING, section=["2 基本规定"]),
        span("p2", "本章说明基本规定。", section=["2 基本规定"]),
    ]
    kept, rejected = filter_unsupported_ai_findings(
        [finding("f", "第1—2章技术内容缺失", ["h1"], category=FindingCategory.COMPLETENESS)], spans, None
    )
    assert kept == []
    assert rejected[0]["reason"] == "FULL_TITLE_TREE_CONTRADICTS_MISSING_CHAPTER"


def test_text_and_table_parameter_conflict_share_one_packet():
    spans = [
        span("p", "改造后地面处理能力为50万m³/d。", section=["9 建设规模"]),
        span("t0", "地面处理能力", block=BlockType.TABLE_CELL, section=["42 附件"], table=1, row=1, col=0),
        span("t1", "40万m³/d", block=BlockType.TABLE_CELL, section=["42 附件"], table=1, row=1, col=1),
    ]
    packets = build_evidence_packets(spans, [], [])
    packet = next(p for p in packets if p.primary_span == "t1")
    assert {"p", "t0", "t1"}.issubset(packet.source_span_ids)


def test_relation_packet_carries_table_headers_labels_units_and_anchor():
    spans = [
        span("title", "参数汇总表", block=BlockType.HEADING, section=["9 建设规模"]),
        span("h0", "参数", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=0, col=0),
        span("h1", "单位", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=0, col=1),
        span("h2", "设计值", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=0, col=2),
        span("r0", "处理能力", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=1, col=0),
        span("r1", "m3/d", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=1, col=1),
        span("r2", "180", block=BlockType.TABLE_CELL, section=["9 建设规模"], table=1, row=1, col=2),
        span("p", "处理能力为180 m3/d", section=["9 建设规模"]),
    ]
    packet = next(item for item in build_evidence_packets(spans, [], []) if "r2" in item.source_span_ids)
    assert packet.relation_type == "TEXT_TABLE"
    assert packet.table_context["column_headers"] == ["参数", "单位", "设计值"]
    assert packet.table_context["row_label"] == "处理能力"
    assert packet.table_context["stable_anchor"].endswith("row:1")
    assert packet.table_context_complete is True
    assert packet.relation_complete is True


def test_equipment_quantity_and_standby_mode_stay_atomic():
    spans = [
        span("e0", "过滤装置", block=BlockType.TABLE_CELL, section=["10 内部集输"], table=2, row=2, col=0),
        span("e1", "2台", block=BlockType.TABLE_CELL, section=["10 内部集输"], table=2, row=2, col=1),
        span("e2", "1用1备", block=BlockType.TABLE_CELL, section=["10 内部集输"], table=2, row=2, col=2),
    ]
    packet = build_evidence_packets(spans, [], [])[0]
    assert set(packet.source_span_ids) == {"e0", "e1", "e2"}
    assert packet.review_topic == "equipment_redundancy"


def test_cross_chapter_conclusion_and_basis_enter_same_batch():
    spans = [
        span("h9", "9 建设规模", block=BlockType.HEADING, section=["9 建设规模"]),
        span("p9", "推荐原址改造方案，建设周期相对较短。", section=["9 建设规模"]),
        span("h4", "4 总论", block=BlockType.HEADING, section=["4 总论"]),
        span("p4", "原址改造方案的推荐结论是周期可控。", section=["4 总论"]),
    ]
    plan = build_evidence_plan(spans, [], [])
    assert any({"p9", "p4"}.issubset(set(batch.source_span_ids)) for batch in plan.batches)


def test_back_half_chapter_receives_a_candidate():
    spans = [
        span("h34", "34 环境保护", block=BlockType.HEADING, section=["34 环境保护"]),
        span("p34", "运行期设备排污应受控收集并明确去向。", section=["34 环境保护"]),
    ]
    packets = build_evidence_packets(spans, [], [])
    assert any("34 环境保护" in path for packet in packets for path in packet.section_paths)


def test_cover_metadata_does_not_consume_most_candidates():
    spans = [span(f"cover{i}", f"封面元数据{i}") for i in range(26)]
    spans += [
        span("h33", "33 安全", block=BlockType.HEADING, section=["33 安全"]),
        span("p33", "接口施工必须停产隔离、检测和监护。", section=["33 安全"]),
        span("h40", "40 投资", block=BlockType.HEADING, section=["40 投资"]),
        span("p40", "总投资4200万元。", section=["40 投资"]),
    ]
    plan = build_evidence_plan(spans, [], [])
    selected = set(plan.selected_span_ids)
    assert "p33" in selected and "p40" in selected
    assert not any(value.startswith("cover") for value in selected)


def test_duplicate_ai_findings_across_batches_are_merged():
    spans = {"s": span("s", "高峰产量55万m³/d超过处理能力50万m³/d")}
    merged, records = deduplicate_findings(
        [], [finding("a", "处理能力不足", ["s"]), finding("b", "高峰产量超过能力", ["s"], category=FindingCategory.CAPACITY)], spans
    )
    assert len(merged) == 1
    assert records == [{"kept_finding_id": "a", "deduplicated_finding_id": "b"}]


def test_ai_deduplication_bounds_evidence_for_one_finding():
    spans = {f"s{index}": span(f"s{index}", f"evidence {index}") for index in range(7)}
    first = finding("a", "capacity issue", [f"s{index}" for index in range(6)])
    second = finding("b", "capacity issue again", ["s0"])

    merged, records = deduplicate_findings([], [first, second], spans)

    assert len(merged) == 1
    assert len(merged[0].evidence_span_ids) == 5
    assert merged[0].original_ai_snapshot["evidence_merge_trimmed_count"] == 1
    assert records == [{"kept_finding_id": "a", "deduplicated_finding_id": "b"}]


def test_rule_finding_wins_when_ai_reports_same_issue():
    spans = {"s": span("s", "高峰产量55万m³/d超过处理能力50万m³/d")}
    rule = finding("rule", "高峰产量需复核", ["s"], origin=Origin.RULE, category=FindingCategory.CAPACITY, parameter="高峰产量")
    ai = finding("ai", "高峰产量超过处理能力", ["s"], category=FindingCategory.CONSISTENCY, parameter="高峰产量")
    merged, records = deduplicate_findings([rule], [ai], spans)
    assert len(merged) == 1 and merged[0].origin is Origin.HYBRID
    assert merged[0].finding_id == "rule"
    assert records[0]["deduplicated_finding_id"] == "ai"


def test_unfounded_taskbook_metadata_requirement_is_rejected():
    spans = [span("s", "编制任务书是唯一事实源。")]
    kept, rejected = filter_unsupported_ai_findings(
        [finding("f", "任务书缺少编号、签发日期和版本", ["s"], category=FindingCategory.TRACEABILITY)], spans, None
    )
    assert kept == [] and rejected[0]["reason"] == "NO_EXPLICIT_METADATA_REQUIREMENT"


def test_review_implementation_has_no_manifest_dependency():
    review_root = Path(__file__).parents[2] / "app" / "review"
    source = "\n".join(path.read_text(encoding="utf-8") for path in review_root.glob("*.py"))
    assert "defect_manifest" not in source
