from __future__ import annotations

from dataclasses import dataclass

from app.parsers.docx_parser import ParsedDocument


@dataclass(frozen=True)
class Section:
    path: list[str]
    title: str
    span_ids: list[str]


def extract_sections(parsed: ParsedDocument) -> list[Section]:
    """Group parsed spans by their section path in document order."""
    grouped: dict[tuple[str, ...], list[str]] = {}
    for span in parsed.spans:
        if span.section_path:
            path = tuple(span.section_path)
            grouped.setdefault(path, []).append(span.span_id)

    return [
        Section(path=list(path), title=path[-1], span_ids=span_ids)
        for path, span_ids in grouped.items()
    ]
