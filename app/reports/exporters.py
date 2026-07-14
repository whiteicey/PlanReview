"""Generate safe, local finding exports without source documents or provider metadata."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from openpyxl import Workbook

from app.review.pipeline import ReviewRun
from app.settings import get_settings

_FINDING_COLUMNS = (
    "finding_id", "origin", "category", "severity", "title", "description",
    "suggestion", "evidence_span_ids", "review_status", "human_note",
)
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_ANONYMOUS_TEXT_FORBIDDEN = re.compile(
    r"(?i)(?:vendor|model|base[_\s-]?url|request[_\s-]?id|api[_\s-]?key|"
    r"authorization|bearer|token|secret|password|raw[_\s-]?doc(?:ument)?|"
    r"(?:https?|wss?)://|(?:[a-z]:[\\/]|/)[^\s]+|\.docx\b)"
)


def _rows(run: ReviewRun) -> list[dict[str, str | None]]:
    """Represent current finding state, including an expert's persisted review."""
    return [
        {
            "finding_id": item.finding_id,
            "origin": item.origin.value,
            "category": item.category,
            "severity": item.severity.value,
            "title": item.title,
            "description": item.description,
            "suggestion": item.suggestion,
            "evidence_span_ids": ", ".join(item.evidence_span_ids),
            "review_status": item.review_status.value,
            "human_note": item.human_note,
        }
        for item in run.findings
    ]


def export_excel(run: ReviewRun, target: Path) -> Path:
    """Write editable review-state rows and evidence references to a spreadsheet."""
    target = _prepare_target(target)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Findings"
    sheet.append([get_settings().disclaimer])
    sheet.append(_FINDING_COLUMNS)
    for row in _rows(run):
        sheet.append([row[column] for column in _FINDING_COLUMNS])
    sheet.freeze_panes = "A3"
    workbook.save(target)
    return target


def export_word(run: ReviewRun, target: Path) -> Path:
    """Write a human-readable report including evidence and expert review state."""
    target = _prepare_target(target)
    document = Document()
    document.add_heading("审查发现", level=0)
    document.add_paragraph(get_settings().disclaimer)
    document.add_paragraph(f"审查状态：{run.final_status}")
    for item in run.findings:
        document.add_heading(item.title, level=1)
        document.add_paragraph(f"问题编号：{item.finding_id}")
        document.add_paragraph(
            f"来源：{item.origin.value}；严重性：{item.severity.value}；"
            f"专家状态：{item.review_status.value}"
        )
        document.add_paragraph(item.description)
        document.add_paragraph(f"建议：{item.suggestion}")
        document.add_paragraph(f"证据 span：{', '.join(item.evidence_span_ids) or '无'}")
        if item.human_note:
            document.add_paragraph(f"专家备注：{item.human_note}")
    document.save(target)
    return target


def export_anonymous_package(run: ReviewRun, target_zip: Path) -> Path:
    """Write a strict allow-list ZIP with de-identified findings and no raw sources."""
    target_zip = _prepare_target(target_zip)
    span_aliases = _span_aliases(run)
    hashes = _anonymous_evidence_hashes(run, span_aliases)
    review_counts = Counter(item.review_status.value for item in run.findings)
    payload = {
        "disclaimer": get_settings().disclaimer,
        "findings": [
            {
                "finding_id": f"finding-{index:04d}",
                "origin": item.origin.value,
                "category": _anonymous_text(item.category, run.case_id),
                "severity": item.severity.value,
                "title": _anonymous_text(item.title, run.case_id),
                "description": _anonymous_text(item.description, run.case_id),
                "suggestion": _anonymous_text(item.suggestion, run.case_id),
                "evidence_span_ids": [span_aliases[span_id] for span_id in item.evidence_span_ids],
                "review_status": item.review_status.value,
                "human_note": _anonymous_text(item.human_note, run.case_id),
            }
            for index, item in enumerate(run.findings, start=1)
        ],
        "rule_versions": [
            {
                "rule_id": f"rule-{index:04d}",
                "version": _anonymous_text(_rule_version(result), run.case_id),
            }
            for index, result in enumerate(_versioned_rule_results(run), start=1)
        ],
        "evidence_text_hashes": hashes,
        "metrics": {
            "finding_count": len(run.findings),
            "review_state_counts": dict(sorted(review_counts.items())),
            "accuracy": "not_measured",
            "recall": "not_measured",
            "time_saved": "not_measured",
            "cost": "not_measured",
        },
    }
    with ZipFile(target_zip, "w", ZIP_DEFLATED) as archive:
        archive.writestr(
            "anonymous-findings.json",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )
    return target_zip


def _prepare_target(target: Path) -> Path:
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _span_aliases(run: ReviewRun) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for finding in run.findings:
        for span_id in finding.evidence_span_ids:
            aliases.setdefault(span_id, f"evidence-{len(aliases) + 1:04d}")
    return aliases


def _versioned_rule_results(run: ReviewRun):
    """Yield one version record per rule, preserving first evaluation order."""
    seen: set[str] = set()
    for result in run.rule_results:
        if result.rule_id not in seen and _rule_version(result) is not None:
            seen.add(result.rule_id)
            yield result


def _rule_version(result) -> str | None:
    if result.rule_version is not None:
        return result.rule_version
    version = result.details.get("rule_version") if isinstance(result.details, dict) else None
    return version if isinstance(version, str) else None


def _anonymous_evidence_hashes(run: ReviewRun, aliases: dict[str, str]) -> dict[str, str]:
    """Export only validated source-text digests, never source text or span IDs."""
    hashes: dict[str, str] = {}
    for span_id, alias in aliases.items():
        text_hash = run.evidence_text_hashes.get(span_id)
        if text_hash is None:
            # Legacy or manually constructed runs may lack source hashes. Do not invent one.
            continue
        if not isinstance(text_hash, str) or _HASH_RE.fullmatch(text_hash) is None:
            raise ValueError("evidence text hash must be a lowercase SHA-256 digest")
        hashes[alias] = text_hash
    return hashes


def _anonymous_text(value: str | None, case_id: str) -> str | None:
    """Keep de-identified prose while redacting values that identify systems or sources."""
    if value is None:
        return None
    if (case_id and case_id in value) or _ANONYMOUS_TEXT_FORBIDDEN.search(value):
        return "[REDACTED]"
    return value
