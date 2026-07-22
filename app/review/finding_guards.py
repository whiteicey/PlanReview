"""Document-wide AI finding guards and cross-batch semantic deduplication."""

from __future__ import annotations

import re

from app.domain.enums import FindingCategory, Origin
from app.domain.schemas import Finding, SourceSpan
from app.extraction.terminology import TerminologyMap

_MISSING_WORDS = ("缺失", "缺少", "未见", "未提供", "不存在")
_RELATION_CATEGORIES = {
    FindingCategory.CONSISTENCY, FindingCategory.AGGREGATION,
    FindingCategory.CROSS_DOMAIN, FindingCategory.CAPACITY,
}
_MAX_FINDING_EVIDENCE_SPANS = 5


def _bounded_evidence(*evidence_lists: list[str]) -> tuple[list[str], int]:
    evidence = list(dict.fromkeys(
        span_id for evidence in evidence_lists for span_id in evidence
    ))
    return evidence[:_MAX_FINDING_EVIDENCE_SPANS], max(
        0, len(evidence) - _MAX_FINDING_EVIDENCE_SPANS
    )


def _bounded_finding_evidence(finding: Finding) -> Finding:
    evidence, trimmed_count = _bounded_evidence(finding.evidence_span_ids)
    if not trimmed_count:
        return finding
    snapshot = dict(finding.original_ai_snapshot)
    snapshot["evidence_merge_trimmed_count"] = (
        int(snapshot.get("evidence_merge_trimmed_count", 0)) + trimmed_count
    )
    snapshot["evidence_limit"] = _MAX_FINDING_EVIDENCE_SPANS
    return finding.model_copy(
        update={"evidence_span_ids": evidence, "original_ai_snapshot": snapshot}
    )


def _chapter_numbers(text: str) -> set[int]:
    numbers: set[int] = set()
    for left, right in re.findall(r"第\s*(\d{1,2})(?:\s*[—-]\s*(\d{1,2}))?\s*章", text):
        start = int(left); end = int(right or left)
        if start <= end <= 99:
            numbers.update(range(start, end + 1))
    return numbers


def _existing_chapters(spans: list[SourceSpan]) -> set[int]:
    found: set[int] = set()
    for span in spans:
        if span.block_type.value != "heading":
            continue
        match = re.match(r"\s*(\d{1,2})(?:\D|$)", span.text)
        if match:
            found.add(int(match.group(1)))
    return found


def _has_explicit_metadata_requirement(spans: list[SourceSpan]) -> bool:
    for span in spans:
        text = span.text
        if "任务书" in text and any(x in text for x in ("编号", "签发日期", "版本")) and any(x in text for x in ("必须", "应当", "要求", "应提供")):
            return True
    return False


def _canonical_terms(text: str, terminology: TerminologyMap | None) -> set[str]:
    normalized = text.replace("预测高峰处理气量", "高峰产量").replace("高峰处理气量", "高峰产量")
    terms: set[str] = set()
    if terminology is not None:
        for canonical, aliases in terminology.canonical_to_aliases.items():
            if any(alias and alias in normalized for alias in aliases):
                terms.add(canonical)
    if "高峰产量" in normalized:
        terms.add("高峰产量")
    return terms


def filter_unsupported_ai_findings(
    findings: list[Finding], spans: list[SourceSpan], terminology: TerminologyMap | None
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Reject assertions contradicted by full-document structure or authority."""
    span_map = {s.span_id: s for s in spans}
    existing = _existing_chapters(spans)
    metadata_required = _has_explicit_metadata_requirement(spans)
    accepted: list[Finding] = []
    rejected: list[dict[str, str]] = []
    for finding in findings:
        prose = f"{finding.title} {finding.description}"
        chapters = _chapter_numbers(prose)
        if chapters and chapters.issubset(existing) and any(word in prose for word in _MISSING_WORDS) and any(word in prose for word in ("章节", "章技术内容", "未见到这些章")):
            rejected.append({"finding_id": finding.finding_id, "reason": "FULL_TITLE_TREE_CONTRADICTS_MISSING_CHAPTER"})
            continue
        if "任务书" in prose and any(word in prose for word in ("编号", "签发日期", "版本")) and any(word in prose for word in _MISSING_WORDS) and not metadata_required:
            rejected.append({"finding_id": finding.finding_id, "reason": "NO_EXPLICIT_METADATA_REQUIREMENT"})
            continue
        if finding.category is FindingCategory.TERMINOLOGY:
            evidence_terms = [_canonical_terms(span_map[sid].text, terminology) for sid in finding.evidence_span_ids if sid in span_map]
            nonempty = [terms for terms in evidence_terms if terms]
            if len(nonempty) >= 2 and len(set.intersection(*nonempty)) == 1:
                rejected.append({"finding_id": finding.finding_id, "reason": "TERMS_NORMALIZE_TO_SAME_CONCEPT"})
                continue
        accepted.append(finding)
    return accepted, rejected


def _tokens(finding: Finding, span_map: dict[str, SourceSpan]) -> set[str]:
    text = f"{finding.parameter or ''} {finding.title} {finding.description}"
    text += " " + " ".join(span_map[sid].text for sid in finding.evidence_span_ids if sid in span_map)
    stop = {"问题", "缺少", "需要", "复核", "报告", "正文", "表格", "存在", "说明"}
    return {x for x in re.findall(r"[\u4e00-\u9fff]{2,10}|DN\d+", text) if x not in stop}


def _same_issue(left: Finding, right: Finding, span_map: dict[str, SourceSpan]) -> bool:
    overlap = set(left.evidence_span_ids).intersection(right.evidence_span_ids)
    if not overlap:
        return False
    if left.parameter and right.parameter and left.parameter == right.parameter:
        return True
    if left.category == right.category:
        return True
    if left.category in _RELATION_CATEGORIES and right.category in _RELATION_CATEGORIES:
        return True
    lt = _tokens(left, span_map); rt = _tokens(right, span_map)
    return bool(lt and rt and len(lt & rt) / min(len(lt), len(rt)) >= 0.5)


def deduplicate_findings(
    rule_findings: list[Finding], ai_findings: list[Finding], span_map: dict[str, SourceSpan]
) -> tuple[list[Finding], list[dict[str, str]]]:
    """Prefer rule findings, then the first AI instance, across all batches."""
    merged = [_bounded_finding_evidence(finding) for finding in rule_findings]
    records: list[dict[str, str]] = []
    for candidate in ai_findings:
        duplicate_index = next((i for i, existing in enumerate(merged) if _same_issue(existing, candidate, span_map)), None)
        if duplicate_index is None:
            merged.append(_bounded_finding_evidence(candidate))
            continue
        base = merged[duplicate_index]
        snapshot = dict(base.original_ai_snapshot)
        prior = list(snapshot.get("deduplicated_from", []))
        prior.append(candidate.finding_id)
        snapshot["deduplicated_from"] = prior
        snapshot.setdefault("llm_supplements", []).append({
            "finding_id": candidate.finding_id,
            "description": candidate.description,
            "suggestion": candidate.suggestion,
            "evidence_span_ids": candidate.evidence_span_ids,
        })
        if base.origin in {Origin.RULE, Origin.HYBRID}:
            merged[duplicate_index] = base.model_copy(update={"origin": Origin.HYBRID, "needs_human_review": True, "original_ai_snapshot": snapshot})
        else:
            evidence, trimmed_count = _bounded_evidence(
                base.evidence_span_ids, candidate.evidence_span_ids
            )
            if trimmed_count:
                snapshot["evidence_merge_trimmed_count"] = (
                    int(snapshot.get("evidence_merge_trimmed_count", 0)) + trimmed_count
                )
                snapshot["evidence_limit"] = _MAX_FINDING_EVIDENCE_SPANS
            merged[duplicate_index] = base.model_copy(update={"evidence_span_ids": evidence, "original_ai_snapshot": snapshot})
        records.append({"kept_finding_id": base.finding_id, "deduplicated_finding_id": candidate.finding_id})
    return merged, records
