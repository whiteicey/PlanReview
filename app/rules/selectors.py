from __future__ import annotations

from app.domain.enums import BlockType
from app.domain.schemas import SourceSpan
from app.parsers.docx_parser import ParsedDocument


def select_spans(
    parsed: ParsedDocument,
    section_contains: str | None = None,
    block_type: BlockType | None = None,
) -> list[SourceSpan]:
    """Select spans using only the supported section and block-type filters.

    Filter values are treated as plain data. In particular, section_contains is
    never parsed or evaluated as an expression.
    """
    result = parsed.spans
    if section_contains is not None:
        result = [
            span
            for span in result
            if any(section_contains in section for section in span.section_path)
        ]
    if block_type is not None:
        result = [span for span in result if span.block_type is block_type]
    return result
