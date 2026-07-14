"""Generate local exports from durable review data without source documents."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from openpyxl import Workbook

from app.review.pipeline import ReviewRun
from app.settings import get_settings


def export_excel(run: ReviewRun, target: Path) -> Path:
    """Write a human-readable spreadsheet from the persisted review run."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Findings"
    sheet.append([get_settings().disclaimer])
    sheet.append([
        "finding_id", "origin", "category", "severity", "title", "description",
        "suggestion", "evidence_span_ids", "review_status", "human_note",
    ])
    for item in run.findings:
        sheet.append([
            item.finding_id, item.origin.value, item.category, item.severity.value,
            item.title, item.description, item.suggestion,
            ", ".join(item.evidence_span_ids), item.review_status.value, item.human_note,
        ])
    workbook.save(target)
    return target


def export_word(run: ReviewRun, target: Path) -> Path:
    """Write a concise Word report from the persisted review run."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    document.add_heading("开发方案审查助手", level=0)
    document.add_paragraph(get_settings().disclaimer)
    document.add_paragraph(f"案例：{run.case_id}")
    document.add_paragraph(f"状态：{run.final_status}")
    for item in run.findings:
        document.add_heading(item.title, level=1)
        document.add_paragraph(f"严重性：{item.severity.value}；专家状态：{item.review_status.value}")
        document.add_paragraph(item.description)
        document.add_paragraph(f"建议：{item.suggestion}")
        document.add_paragraph(f"证据：{', '.join(item.evidence_span_ids)}")
        if item.human_note:
            document.add_paragraph(f"专家备注：{item.human_note}")
    document.save(target)
    return target


def export_anonymous_package(run: ReviewRun, target_zip: Path) -> Path:
    """Write a ZIP containing only anonymized persisted review metadata."""
    target_zip = Path(target_zip)
    target_zip.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "disclaimer": get_settings().disclaimer,
        "final_status": run.final_status,
        "finding_count": len(run.findings),
        "findings": [
            {
                "finding_id": item.finding_id,
                "origin": item.origin.value,
                "category": item.category,
                "severity": item.severity.value,
                "title": item.title,
                "description": item.description,
                "suggestion": item.suggestion,
                "evidence_span_ids": item.evidence_span_ids,
                "review_status": item.review_status.value,
                "human_note": item.human_note,
            }
            for item in run.findings
        ],
    }
    with ZipFile(target_zip, "w", ZIP_DEFLATED) as archive:
        archive.writestr("anonymous-findings.json", json.dumps(payload, ensure_ascii=False))
    return target_zip
