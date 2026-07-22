"""Structured, document-wide evidence packets and safe dynamic LLM batches."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from collections import defaultdict

from app.domain.enums import BlockType
from app.domain.schemas import ParameterFact, RuleResult, SourceSpan
from app.llm.limits import MAX_LLM_EVIDENCE_IDS, MAX_LLM_TOTAL_CHARACTERS

EVIDENCE_SELECTOR_VERSION = "structured-packets-v1.2"

MAX_AI_BATCHES = 6
TARGET_PACKET_COUNT_MIN = 120
TARGET_PACKET_COUNT_MAX = 250
TARGET_TOTAL_CHARACTERS_MIN = 50_000
TARGET_TOTAL_CHARACTERS_MAX = 80_000
TARGET_BATCH_CHARACTERS_MIN = 8_000
TARGET_BATCH_CHARACTERS_MAX = 15_000
TARGET_BATCH_SPANS_MAX = 120
RELATION_PACKET_BUDGET_RATIO = 0.70

RELATION_TEXT_TABLE = "TEXT_TABLE"
RELATION_SUMMARY_DETAIL = "SUMMARY_DETAIL"
RELATION_DEMAND_CAPACITY = "DEMAND_CAPACITY"
RELATION_CONCLUSION_BASIS = "CONCLUSION_BASIS"

# One batch per group keeps related evidence together while reserving budget for
# all seven required review areas.  The two paired groups are intentionally
# cross-domain and still preserve each packet atomically.
_BATCH_TOPIC_GROUPS = (
    ("completeness_reference", "capacity_process"),
    ("equipment_redundancy",),
    ("pipeline_quantities", "digital_power_fire"),
    ("safety_environment_health",),
    ("schedule_investment", "option_conclusion"),
)

REVIEW_TOPICS = (
    "completeness_reference",
    "capacity_process",
    "equipment_redundancy",
    "pipeline_quantities",
    "digital_power_fire",
    "safety_environment_health",
    "schedule_investment",
    "option_conclusion",
)

_NUMBER_OR_UNIT = re.compile(r"(?:\d|MPa|kPa|万?m[³3]?/d|km|mm|万元|个月|℃|DN\s*\d+)", re.I)
_REFERENCE = re.compile(r"(?:第\s*\d+\s*章|表\s*\d+(?:[-—]\d+)?|附表|附件|任务书|依据)")
_RISK_TERMS = (
    "推荐", "结论", "不适用", "待确认", "不得", "必须", "应当", "应", "无需", "直接排放",
    "同时运行", "备用", "检修", "隔离", "报警", "能力", "投资", "进度", "接口", "风险", "措施",
)


@dataclass(frozen=True)
class EvidencePacket:
    packet_id: str
    review_topic: str
    target_concept: str
    section_paths: list[list[str]]
    source_span_ids: list[str]
    primary_span: str
    related_spans: list[str]
    candidate_reason: str
    priority_score: float
    estimated_characters: int
    relation_type: str | None = None
    table_context: dict = field(default_factory=dict)
    relation_complete: bool = False
    table_context_complete: bool = False
    comparison_sides_present: bool = False
    missing_context_reason: str | None = None
    stable_evidence_anchors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class EvidenceBatch:
    batch_id: str
    review_topic: str
    packet_ids: list[str]
    source_span_ids: list[str]
    primary_span_ids: list[str]
    user_content: str
    estimated_characters: int


@dataclass(frozen=True)
class EvidencePlan:
    available_span_count: int
    packets: list[EvidencePacket]
    batches: list[EvidenceBatch]
    title_span_ids: list[str]
    selection_diagnostics: dict
    packet_lifecycle_entries: list[dict] = field(default_factory=list)

    @property
    def selected_span_ids(self) -> list[str]:
        seen: set[str] = set()
        return [sid for packet in self.packets for sid in packet.source_span_ids if not (sid in seen or seen.add(sid))]


def _chapter(span: SourceSpan) -> int | None:
    first = span.section_path[0] if span.section_path else span.text if span.block_type is BlockType.HEADING else ""
    match = re.match(r"\s*(\d{1,2})(?:\D|$)", first)
    return int(match.group(1)) if match else None


def _topic(span: SourceSpan, text: str) -> str:
    chapter = _chapter(span)
    if chapter in (39, 40) or any(x in text for x in ("投资", "费用", "工期", "进度", "M1", "M2")):
        return "schedule_investment"
    if chapter in (33, 34, 35, 36, 37) or any(x in text for x in ("安全", "环保", "职业卫生", "水土保持", "节能")):
        return "safety_environment_health"
    if chapter in (14, 15, 16, 17) or any(x in text for x in ("站控", "供电", "UPS", "消防", "报警", "可燃气")):
        return "digital_power_fire"
    if chapter == 9 or any(x in text for x in ("方案比选", "推荐方案", "推荐", "相对较")):
        return "option_conclusion"
    if any(x in text for x in ("管线", "管道", "DN", "管径", "长度", "工程量")):
        return "pipeline_quantities"
    if any(x in text for x in ("设备", "分离器", "过滤装置", "计量装置", "台", "套", "用", "备")):
        return "equipment_redundancy"
    if chapter in (9, 10) or any(x in text for x in ("处理能力", "高峰产量", "处理气量", "设计压力", "规模")):
        return "capacity_process"
    return "completeness_reference"


def _concept(texts: list[str]) -> str:
    for value in texts:
        value = re.sub(r"\s+", " ", value).strip(" ：:，,；;")
        if value and not _NUMBER_OR_UNIT.fullmatch(value):
            return value[:80]
    return "未命名审查对象"


def _interesting(span: SourceSpan) -> bool:
    text = span.text.strip()
    return bool(text and (_NUMBER_OR_UNIT.search(text) or _REFERENCE.search(text) or any(term in text for term in _RISK_TERMS)))


def _unique(values):
    seen = set()
    return [value for value in values if value and not (value in seen or seen.add(value))]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.casefold()
    return any(term.casefold() in lowered for term in terms)


def _table_context(
    cells: list[SourceSpan],
    all_spans: list[SourceSpan],
    positions: dict[str, int],
) -> tuple[dict, bool, list[str]]:
    """Build bounded table context without guessing missing headers or titles."""
    if not cells or cells[0].table_index is None:
        return {}, False, []
    document_id = cells[0].document_id
    table_index = cells[0].table_index
    table_cells = [
        span for span in all_spans
        if span.document_id == document_id and span.table_index == table_index
    ]
    headers = [
        span.text.strip() for span in sorted(
            (item for item in table_cells if item.row_index == 0),
            key=lambda item: item.column_index if item.column_index is not None else -1,
        ) if span.text.strip()
    ]
    row_labels = [
        span.text.strip() for span in sorted(cells, key=lambda item: item.column_index if item.column_index is not None else -1)
        if span.text.strip()
    ]
    row_label = row_labels[0] if row_labels else None
    units = [text for text in row_labels if re.search(r"(?:MPa|kPa|Pa|mm|cm|km|m\b|元|万元|月|天|h|%|°C)", text, re.I)]
    values = [text for text in row_labels if _NUMBER_OR_UNIT.search(text)]
    first_position = min(positions.get(item.span_id, 0) for item in cells)
    title = None
    for item in reversed(all_spans[:first_position]):
        if item.document_id != document_id:
            continue
        if item.block_type is BlockType.HEADING:
            title = item.text.strip()
            break
        if item.block_type is BlockType.PARAGRAPH and item.text.strip() and len(item.text.strip()) < 180:
            title = item.text.strip()
            break
    context = {
        "table_index": table_index,
        "table_title": title,
        "column_headers": _unique(headers)[:16],
        "row_label": row_label,
        "row_cells": row_labels[:32],
        "units": _unique(units)[:8],
        "values": values[:16],
        "stable_anchor": f"{document_id}:table:{table_index}:row:{cells[0].row_index}",
    }
    complete = bool(title and headers and row_label and values)
    missing = []
    if not title:
        missing.append("TABLE_TITLE_MISSING")
    if not headers:
        missing.append("COLUMN_HEADERS_MISSING")
    if not row_label:
        missing.append("ROW_LABEL_MISSING")
    if not values:
        missing.append("VALUE_MISSING")
    return context, complete, missing


def _relation_kind(primary_text: str, related_texts: list[str], *, table_present: bool) -> str | None:
    combined = " ".join([primary_text, *related_texts])
    if table_present:
        summary_terms = ("total", "sum", "subtotal", "合计", "总计", "小计", "汇总")
        if _contains_any(combined, summary_terms):
            return RELATION_SUMMARY_DETAIL
        if any(not _NUMBER_OR_UNIT.search(text) for text in related_texts) and _NUMBER_OR_UNIT.search(combined):
            return RELATION_TEXT_TABLE
    demand_terms = ("demand", "required", "requirement", "design need", "需求", "需要", "设计能力")
    capacity_terms = ("capacity", "throughput", "处理能力", "能力", "规模")
    if _contains_any(combined, demand_terms) and _contains_any(combined, capacity_terms):
        return RELATION_DEMAND_CAPACITY
    conclusion_terms = ("conclusion", "recommend", "recommended", "basis", "according", "结论", "推荐", "依据", "比选")
    if _contains_any(combined, conclusion_terms) and len(related_texts) >= 1:
        return RELATION_CONCLUSION_BASIS
    return None


def build_evidence_packets(
    spans: list[SourceSpan],
    facts: list[ParameterFact],
    rule_results: list[RuleResult],
) -> list[EvidencePacket]:
    """Build generic candidates from document structure; no test truth is accepted."""
    available = [s for s in spans if s.text and s.span_id]
    by_id = {s.span_id: s for s in available}
    positions = {s.span_id: i for i, s in enumerate(available)}
    rows: dict[tuple[str, int, int], list[SourceSpan]] = defaultdict(list)
    for span in available:
        if span.block_type is BlockType.TABLE_CELL and span.table_index is not None and span.row_index is not None:
            rows[(span.document_id, span.table_index, span.row_index)].append(span)

    fact_ids = {fact.source_span_id for fact in facts}
    rule_ids = {sid for result in rule_results if result.status.value != "PASS" for sid in result.evidence_span_ids}
    packets: list[EvidencePacket] = []
    used_primary: set[str] = set()

    def add(
        primary: SourceSpan,
        related: list[SourceSpan],
        reason: str,
        score: float,
        concept: str | None = None,
        *,
        relation_type: str | None = None,
        table_context: dict | None = None,
        relation_complete: bool = False,
        table_context_complete: bool = False,
        comparison_sides_present: bool | None = None,
        missing_context_reason: str | None = None,
        infer_relation: bool = True,
    ) -> None:
        if primary.span_id in used_primary:
            return
        related_ids = _unique(s.span_id for s in related if s.span_id != primary.span_id)[:8]
        source_ids = [primary.span_id, *related_ids]
        sections = []
        for sid in source_ids:
            path = by_id[sid].section_path
            if path and path not in sections:
                sections.append(path)
        combined = " ".join(by_id[sid].text for sid in source_ids)
        if infer_relation and relation_type is None:
            relation_type = _relation_kind(
                primary.text,
                [by_id[sid].text for sid in related_ids],
                table_present=primary.block_type is BlockType.TABLE_CELL or any(
                    by_id[sid].block_type is BlockType.TABLE_CELL for sid in related_ids
                ),
            )
        comparison_sides_present = (
            len(source_ids) > 1
            if comparison_sides_present is None
            else bool(comparison_sides_present)
        )
        if relation_type and not relation_complete:
            relation_complete = comparison_sides_present
        if relation_type and missing_context_reason is None and not relation_complete:
            missing_context_reason = "COMPARISON_SIDE_MISSING"
        packet = EvidencePacket(
            packet_id=f"packet-{len(packets):04d}",
            review_topic=_topic(primary, combined),
            target_concept=concept or _concept([primary.text, *(s.text for s in related)]),
            section_paths=sections,
            source_span_ids=source_ids,
            primary_span=primary.span_id,
            related_spans=related_ids,
            candidate_reason=reason,
            priority_score=score,
            estimated_characters=min(1200, len(combined) + 160),
            relation_type=relation_type,
            table_context=dict(table_context or {}),
            relation_complete=relation_complete,
            table_context_complete=table_context_complete,
            comparison_sides_present=comparison_sides_present,
            missing_context_reason=missing_context_reason,
            stable_evidence_anchors=[
                f"{by_id[sid].document_id}:{by_id[sid].block_type.value}:{by_id[sid].paragraph_index if by_id[sid].paragraph_index is not None else by_id[sid].table_index}"
                for sid in source_ids[:8]
            ],
        )
        packets.append(packet)
        used_primary.add(primary.span_id)

    # Every non-empty table row is an atomic packet. This keeps labels, values,
    # units, quantities and operating modes together instead of sampling cells.
    for key in sorted(rows, key=lambda k: min(positions[s.span_id] for s in rows[k])):
        cells = sorted(rows[key], key=lambda s: s.column_index if s.column_index is not None else -1)
        if not any(s.text.strip() for s in cells):
            continue
        primary = next((s for s in cells if s.column_index not in (None, 0) and s.text.strip()), cells[0])
        concept = _concept([s.text for s in cells])
        cell_ids = {c.span_id for c in cells}
        cross = [
            s for s in available
            if s.span_id not in cell_ids
            and len(concept) >= 2
            and concept in s.text
        ][:4]
        context, context_complete, missing_context = _table_context(cells, available, positions)
        summary_terms = ("total", "sum", "subtotal", "合计", "总计", "小计", "汇总")
        is_summary = _contains_any(" ".join(s.text for s in cells), summary_terms)
        if is_summary:
            detail_rows = [
                item for row_key, row_cells in rows.items()
                if row_key[:2] == key[:2] and row_key[2] != key[2]
                for item in row_cells
            ]
            cross.extend(detail_rows[:12])
        relation = (
            RELATION_SUMMARY_DETAIL if is_summary else
            RELATION_TEXT_TABLE if cross else None
        )
        score = 90.0 if primary.span_id in fact_ids or primary.span_id in rule_ids else 68.0
        add(
            primary,
            [*cells, *cross],
            "TABLE_ROW_RELATION",
            score,
            concept,
            relation_type=relation,
            table_context=context,
            relation_complete=context_complete and (bool(cross) or is_summary),
            table_context_complete=context_complete,
            missing_context_reason=";".join(missing_context) if missing_context else None,
            infer_relation=False,
        )

    # Cross-paragraph relation packets keep both sides of semantic checks in
    # one atomic candidate.  Matching is based on document evidence terms and
    # section context, never on test manifests or fixed coordinates.
    relation_pairs = (
        (
            RELATION_DEMAND_CAPACITY,
            ("demand", "required", "requirement", "需求", "需要", "设计需求"),
            ("capacity", "throughput", "处理能力", "能力", "规模"),
            "DEMAND_CAPACITY_RELATION",
        ),
        (
            RELATION_CONCLUSION_BASIS,
            ("conclusion", "recommend", "recommended", "结论", "推荐"),
            ("basis", "according", "criteria", "依据", "比选", "理由"),
            "CONCLUSION_BASIS_RELATION",
        ),
    )
    prose_spans = [span for span in available if span.block_type is BlockType.PARAGRAPH and span.text.strip()]
    for relation_type, left_terms, right_terms, reason in relation_pairs:
        for primary in prose_spans:
            if primary.span_id in used_primary:
                continue
            primary_left = _contains_any(primary.text, left_terms)
            primary_right = _contains_any(primary.text, right_terms)
            if not (primary_left or primary_right):
                continue
            related = next(
                (
                    candidate for candidate in prose_spans
                    if candidate.span_id != primary.span_id
                    and (
                        (primary_left and _contains_any(candidate.text, right_terms))
                        or (primary_right and _contains_any(candidate.text, left_terms))
                    )
                    and (
                        candidate.section_path == primary.section_path
                        or candidate.document_id == primary.document_id
                    )
                ),
                None,
            )
            if related is None:
                continue
            add(
                primary,
                [related],
                reason,
                82.0,
                _concept([primary.text, related.text]),
                relation_type=relation_type,
                relation_complete=True,
                comparison_sides_present=True,
            )
            break

    # Risk-bearing prose gets local context and cross-section occurrences.
    for span in available:
        if span.block_type is not BlockType.PARAGRAPH or not span.section_path or not _interesting(span):
            continue
        index = positions[span.span_id]
        neighbors = [available[i] for i in (index - 1, index + 1) if 0 <= i < len(available) and available[i].document_id == span.document_id]
        tokens = [t for t in re.findall(r"[\u4e00-\u9fff]{3,12}", span.text) if t not in ("本报告", "本项目")][:2]
        cross = [s for s in available if s.span_id != span.span_id and any(t in s.text for t in tokens)][:3]
        score = 88.0 if span.span_id in fact_ids or span.span_id in rule_ids else 64.0
        add(span, [*neighbors, *cross], "RISK_OR_RELATION_PROSE", score)

    # One packet per top-level chapter guarantees coverage of the back half and
    # carries the real heading tree, preventing batch-local absence claims.
    top_groups: dict[tuple[str, int], list[SourceSpan]] = defaultdict(list)
    for span in available:
        chapter = _chapter(span)
        if chapter is not None:
            top_groups[(span.document_id, chapter)].append(span)
    for (_doc, chapter), group in sorted(top_groups.items(), key=lambda kv: min(positions[s.span_id] for s in kv[1])):
        headings = [s for s in group if s.block_type is BlockType.HEADING]
        if not headings:
            continue
        primary = headings[0]
        samples = [*headings[1:6], *[s for s in group if s.block_type is BlockType.PARAGRAPH][:4]]
        add(primary, samples, "CHAPTER_STRUCTURE_AND_CONTENT", 55.0, primary.text)

    return packets


def _render_packet(packet: EvidencePacket, span_map: dict[str, SourceSpan]) -> str:
    primary = span_map[packet.primary_span]
    primary_limit = 480 if packet.relation_type else 340
    related_limit = 240 if packet.relation_type else 50
    lines = [
        f"[{packet.primary_span}]",
        f"证据包 {packet.packet_id}；主题={packet.review_topic}；对象={packet.target_concept}；原因={packet.candidate_reason}",
        "核对要求：本证据包为不可拆分的业务关系，请同时核对对象、数值、单位、适用范围、运行方式、引用依据和结论。",
        f"主证据（{' / '.join(primary.section_path)}）：{primary.text[:primary_limit]}",
    ]
    if packet.relation_type or packet.table_context or packet.missing_context_reason:
        lines.insert(
            1,
            f"relation_type={packet.relation_type or 'ORDINARY'}; relation_complete={packet.relation_complete}; table_context_complete={packet.table_context_complete}; missing_context_reason={packet.missing_context_reason or 'NONE'}",
        )
    if packet.table_context:
        context = packet.table_context
        lines.append(
            "table_context="
            f"title:{context.get('table_title') or 'UNKNOWN'}; "
            f"headers:{' | '.join(context.get('column_headers') or []) or 'UNKNOWN'}; "
            f"row_label:{context.get('row_label') or 'UNKNOWN'}; "
            f"units:{' | '.join(context.get('units') or []) or 'UNKNOWN'}; "
            f"values:{' | '.join(context.get('values') or []) or 'UNKNOWN'}; "
            f"anchor:{context.get('stable_anchor') or 'UNKNOWN'}"
        )
    for sid in packet.related_spans[:6]:
        span = span_map[sid]
        location = " / ".join(span.section_path)
        lines.append(f"关联证据（{location}）：{span.text[:related_limit]}")
    return "\n".join(lines)[:1600]


def _normalized_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", value.casefold(), flags=re.UNICODE)


def _packet_chapter(packet: EvidencePacket, span_map: dict[str, SourceSpan]) -> int | None:
    return _chapter(span_map[packet.primary_span])


def _packet_rank(packet: EvidencePacket) -> tuple:
    relation_bonus = 20 if packet.relation_type is not None else 0
    multi_span_bonus = min(12, len(packet.related_spans) * 2)
    return (-(packet.priority_score + relation_bonus + multi_span_bonus), packet.packet_id)


def _deduplicate_and_filter(
    packets: list[EvidencePacket], span_map: dict[str, SourceSpan]
) -> tuple[list[EvidencePacket], dict[str, int], dict[str, dict]]:
    """Remove noise before budgeting; never uses ground truth or document order."""
    reasons = {
        "empty_or_title_only": 0,
        "duplicate_text": 0,
        "repeated_table_header": 0,
    }
    selected: list[EvidencePacket] = []
    decisions: dict[str, dict] = {}
    seen_text: set[tuple[str, str]] = set()
    seen_headers: set[str] = set()
    for packet in sorted(packets, key=_packet_rank):
        primary = span_map[packet.primary_span]
        primary_text = primary.text.strip()
        normalized = _normalized_text(primary_text)
        if not normalized:
            reasons["empty_or_title_only"] += 1
            decisions[packet.packet_id] = {"stage": "FILTER", "decision": "DROPPED", "reason_code": "EMPTY_OR_TITLE_ONLY"}
            continue
        if (
            packet.candidate_reason == "CHAPTER_STRUCTURE_AND_CONTENT"
            and len(packet.source_span_ids) <= 1
        ):
            reasons["empty_or_title_only"] += 1
            decisions[packet.packet_id] = {"stage": "FILTER", "decision": "DROPPED", "reason_code": "TITLE_ONLY"}
            continue
        key = (packet.review_topic, normalized)
        if key in seen_text:
            reasons["duplicate_text"] += 1
            decisions[packet.packet_id] = {"stage": "DEDUPLICATION", "decision": "DROPPED", "reason_code": "DUPLICATE_TEXT"}
            continue
        # Repeated short first-row labels such as 序号/名称/单位 are metadata,
        # not independent business evidence.  Relational rows remain intact.
        if (
            primary.block_type is BlockType.TABLE_CELL
            and primary.row_index == 0
            and len(normalized) <= 24
        ):
            if normalized in seen_headers:
                reasons["repeated_table_header"] += 1
                decisions[packet.packet_id] = {"stage": "FILTER", "decision": "DROPPED", "reason_code": "REPEATED_TABLE_HEADER"}
                continue
            seen_headers.add(normalized)
        seen_text.add(key)
        selected.append(packet)
        decisions[packet.packet_id] = {"stage": "FILTER", "decision": "KEPT", "reason_code": "PASSED_FILTER"}
    return selected, reasons, decisions


def _diverse_topic_selection(
    candidates: list[EvidencePacket],
    span_map: dict[str, SourceSpan],
    *,
    max_packets: int,
    max_characters: int,
) -> list[EvidencePacket]:
    """Round-robin chapters so late sections cannot be starved by early ones."""
    buckets: dict[int | None, list[EvidencePacket]] = defaultdict(list)
    for packet in candidates:
        buckets[_packet_chapter(packet, span_map)].append(packet)
    for bucket in buckets.values():
        bucket.sort(key=_packet_rank)
    chapter_order = sorted(buckets, key=lambda value: (value is None, value or 0))
    chosen: list[EvidencePacket] = []
    rendered_characters = 0
    while chapter_order and len(chosen) < max_packets:
        progressed = False
        for chapter in list(chapter_order):
            bucket = buckets[chapter]
            if not bucket:
                chapter_order.remove(chapter)
                continue
            packet = bucket.pop(0)
            size = len(_render_packet(packet, span_map)) + (2 if chosen else 0)
            if rendered_characters + size > max_characters:
                continue
            chosen.append(packet)
            rendered_characters += size
            progressed = True
            if len(chosen) >= max_packets:
                break
        if not progressed:
            break
    return chosen


def build_evidence_plan(
    spans: list[SourceSpan], facts: list[ParameterFact], rule_results: list[RuleResult]
) -> EvidencePlan:
    raw_packets = build_evidence_packets(spans, facts, rule_results)
    span_map = {s.span_id: s for s in spans}
    filtered, removal_reasons, packet_decisions = _deduplicate_and_filter(raw_packets, span_map)
    selected_packets: list[EvidencePacket] = []
    batches: list[EvidenceBatch] = []
    selected_packet_ids: set[str] = set()
    for group_index, topics in enumerate(_BATCH_TOPIC_GROUPS, 1):
        group_candidates = [
            packet for packet in filtered
            if packet.packet_id not in selected_packet_ids
            and packet.review_topic in topics
        ]
        if not group_candidates:
            # A document may not contain a dedicated completeness/capacity
            # topic. Borrow the highest-ranked remaining candidates so that
            # the batch budget and late-chapter coverage are still exercised.
            group_candidates = [
                packet for packet in filtered
                if packet.packet_id not in selected_packet_ids
            ]
        # Balance paired topics first, then fill unused capacity from the same
        # group.  This is topic/chapter quota selection, not a prefix cutoff.
        chosen: list[EvidencePacket] = []
        per_topic_cap = MAX_LLM_EVIDENCE_IDS // len(topics)
        for topic in topics:
            chosen.extend(_diverse_topic_selection(
                [packet for packet in group_candidates if packet.review_topic == topic],
                span_map,
                max_packets=per_topic_cap,
                max_characters=TARGET_BATCH_CHARACTERS_MAX // len(topics),
            ))
        chosen_ids = {packet.packet_id for packet in chosen}
        if len(chosen) < MAX_LLM_EVIDENCE_IDS:
            remainder = _diverse_topic_selection(
                [packet for packet in group_candidates if packet.packet_id not in chosen_ids],
                span_map,
                max_packets=MAX_LLM_EVIDENCE_IDS - len(chosen),
                max_characters=TARGET_BATCH_CHARACTERS_MAX,
            )
            for packet in remainder:
                projected = len("\n\n".join(_render_packet(item, span_map) for item in [*chosen, packet]))
                if projected <= TARGET_BATCH_CHARACTERS_MAX:
                    chosen.append(packet)
        # Enforce the operational span budget without splitting a relational
        # packet. The input order was already produced by topic/chapter
        # round-robin selection, so this is not document-prefix truncation.
        budgeted: list[EvidencePacket] = []
        budgeted_span_ids: set[str] = set()
        budgeted_characters = 0
        relation_count = 0
        relation_limit = int(len(chosen) * RELATION_PACKET_BUDGET_RATIO)
        ordinary_available = any(packet.relation_type is None for packet in group_candidates)
        for packet in chosen:
            rendered_size = len(_render_packet(packet, span_map)) + (2 if budgeted else 0)
            next_span_ids = budgeted_span_ids | set(packet.source_span_ids)
            if packet.relation_type is not None and relation_count >= relation_limit:
                continue
            if len(next_span_ids) > TARGET_BATCH_SPANS_MAX:
                continue
            if budgeted_characters + rendered_size > TARGET_BATCH_CHARACTERS_MAX:
                continue
            budgeted.append(packet)
            budgeted_span_ids = next_span_ids
            budgeted_characters += rendered_size
            if packet.relation_type is not None:
                relation_count += 1
        if ordinary_available and budgeted and all(packet.relation_type is not None for packet in budgeted):
            ordinary = next((packet for packet in chosen if packet.relation_type is None), None)
            if ordinary is not None:
                replacement = budgeted[-1]
                projected = budgeted_characters - len(_render_packet(replacement, span_map)) + len(_render_packet(ordinary, span_map))
                if projected <= TARGET_BATCH_CHARACTERS_MAX:
                    budgeted[-1] = ordinary
                    budgeted_characters = projected
        chosen = budgeted
        if not chosen:
            continue
        rendered = [_render_packet(packet, span_map) for packet in chosen]
        content = "\n\n".join(rendered)
        selected_packets.extend(chosen)
        selected_packet_ids.update(packet.packet_id for packet in chosen)
        batches.append(EvidenceBatch(
            batch_id=f"v111-{group_index:02d}",
            review_topic="+".join(topics),
            packet_ids=[packet.packet_id for packet in chosen],
            source_span_ids=_unique(sid for packet in chosen for sid in packet.source_span_ids),
            primary_span_ids=[packet.primary_span for packet in chosen],
            user_content=content,
            estimated_characters=len(content),
        ))
    if len(batches) > MAX_AI_BATCHES:
        raise ValueError("evidence plan exceeded the six-batch hard limit")
    selected_batch: dict[str, str] = {}
    for batch in batches:
        for packet_id in batch.packet_ids:
            selected_batch[packet_id] = batch.batch_id
    lifecycle_entries: list[dict] = []
    for packet in raw_packets:
        decision = packet_decisions.get(packet.packet_id, {
            "stage": "BUDGET",
            "decision": "DROPPED",
            "reason_code": "BUDGET_OR_QUOTA",
        })
        if packet.packet_id in selected_batch:
            decision = {
                "stage": "FINAL_SELECTION",
                "decision": "SELECTED",
                "reason_code": "SELECTED_FOR_BATCH",
                "batch_id": selected_batch[packet.packet_id],
            }
        elif decision.get("decision") == "KEPT":
            decision = {
                "stage": "BUDGET",
                "decision": "DROPPED",
                "reason_code": "BUDGET_OR_QUOTA",
            }
        primary = span_map.get(packet.primary_span)
        lifecycle_entries.append({
            "packet_id": packet.packet_id,
            "packet_type": packet.relation_type or "ORDINARY",
            "generation_reason": packet.candidate_reason,
            "source_span_ids": list(packet.source_span_ids),
            "stage": decision["stage"],
            "decision": decision["decision"],
            "reason_code": decision["reason_code"],
            "priority": packet.priority_score,
            "topic": packet.review_topic,
            "chapter": _chapter(primary) if primary is not None else None,
            "character_count": packet.estimated_characters,
            "batch_id": decision.get("batch_id"),
            "attempt_number": 1,
            "created_sequence": len(lifecycle_entries) + 1,
            "relation_complete": packet.relation_complete,
            "table_context_complete": packet.table_context_complete,
            "comparison_sides_present": packet.comparison_sides_present,
            "missing_context_reason": packet.missing_context_reason,
            "stable_evidence_anchors": list(packet.stable_evidence_anchors),
        })
    title_ids = [s.span_id for s in spans if s.block_type is BlockType.HEADING]
    topic_counts = {
        topic: sum(1 for packet in selected_packets if packet.review_topic == topic)
        for topic in REVIEW_TOPICS
    }
    chapters = sorted({chapter for packet in selected_packets if (chapter := _packet_chapter(packet, span_map)) is not None})
    relation_packet_count = sum(1 for packet in selected_packets if packet.relation_type is not None)
    ordinary_packet_count = max(0, len(selected_packets) - relation_packet_count)
    diagnostics = {
        "raw_packet_count": len(raw_packets),
        "filtered_packet_count": len(filtered),
        "final_packet_count": len(selected_packets),
        "removed_packet_count": len(raw_packets) - len(selected_packets),
        "removal_reasons": {
            **removal_reasons,
            "budget_and_quota": max(0, len(filtered) - len(selected_packets)),
        },
        "topic_packet_counts": topic_counts,
        "covered_chapters": chapters,
        "covered_chapter_count": len(chapters),
        "batch_count": len(batches),
        "total_characters": sum(batch.estimated_characters for batch in batches),
        "relation_packet_count": relation_packet_count,
        "ordinary_packet_count": ordinary_packet_count,
        "relation_packet_ratio": round(relation_packet_count / len(selected_packets), 4) if selected_packets else 0.0,
        "relation_packet_budget_ratio": RELATION_PACKET_BUDGET_RATIO,
        "relation_types": {
            relation_type: sum(1 for packet in selected_packets if packet.relation_type == relation_type)
            for relation_type in (
                RELATION_TEXT_TABLE,
                RELATION_SUMMARY_DETAIL,
                RELATION_DEMAND_CAPACITY,
                RELATION_CONCLUSION_BASIS,
            )
        },
    }
    return EvidencePlan(
        len([s for s in spans if s.text and s.span_id]),
        selected_packets,
        batches,
        title_ids,
        diagnostics,
        lifecycle_entries,
    )


def expand_packet_evidence(finding_ids: list[str], batch: EvidenceBatch, packets: dict[str, EvidencePacket]) -> list[str]:
    by_primary = {packets[pid].primary_span: packets[pid] for pid in batch.packet_ids}
    return _unique(sid for fid in finding_ids if fid in by_primary for sid in by_primary[fid].source_span_ids)
