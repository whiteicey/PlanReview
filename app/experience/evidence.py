"""Bounded evidence resolver with hash and anchor verification."""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.experience.repository import ExperienceRepository
from app.parsers.docx_parser import DocxParser
from app.persistence.models import ExpertExperienceSummaryJobORM, FindingORM
from app.review.background_jobs import cache_key
from app.review.parsed_cache import ParsedDocumentCache
from app.review.pipeline import format_span_location
from app.settings import Settings
from app.storage.paths import safe_join

MAX_EVIDENCE_SPANS = 3
MAX_EVIDENCE_CHARACTERS = 1200


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_evidence(
    repository: ExperienceRepository,
    job: ExpertExperienceSummaryJobORM,
    finding: FindingORM,
    parsed_cache: ParsedDocumentCache,
    settings: Settings,
) -> list[dict]:
    span_ids = list(finding.evidence_span_ids[:MAX_EVIDENCE_SPANS])
    if not span_ids:
        return []
    run = finding.review_run
    expected_hashes = run.evidence_text_hashes or {}
    expected_locations = run.evidence_locations or {}
    persisted = repository.persisted_evidence(job, span_ids)
    if persisted and all(
        item["text_hash"] == expected_hashes.get(item["span_id"])
        and item["location"] == expected_locations.get(item["span_id"])
        for item in persisted
    ):
        return persisted

    case = run.case
    files = list(case.files)
    file_paths = [safe_join(settings.storage_root, *item.storage_relative_path.split("/")) for item in files]
    for stored, path in zip(files, file_paths, strict=True):
        if not path.is_file() or _sha256(path) != stored.sha256:
            raise ValueError("原DOCX哈希校验失败，未调用模型")

    documents = parsed_cache.get(case.case_id, cache_key(case))
    if documents is None:
        documents = [
            DocxParser().parse(path, document_id=f"{case.case_id}-{index}")
            for index, path in enumerate(file_paths)
        ]
        parsed_cache.put(case.case_id, cache_key(case), documents)
    spans = {span.span_id: (span, files[index].sha256) for index, document in enumerate(documents) for span in document.spans}
    snapshot: list[dict] = []
    for span_id in span_ids:
        value = spans.get(span_id)
        if value is None:
            raise ValueError("证据锚点不存在，未调用模型")
        span, document_sha = value
        location = format_span_location(span)
        if span.text_hash != expected_hashes.get(span_id) or location != expected_locations.get(span_id):
            raise ValueError("证据哈希或位置锚点不一致，未调用模型")
        snapshot.append({
            "span_id": span_id,
            "text": span.text[:MAX_EVIDENCE_CHARACTERS],
            "text_hash": span.text_hash,
            "location": location,
            "document_sha256": document_sha,
        })
    return snapshot

