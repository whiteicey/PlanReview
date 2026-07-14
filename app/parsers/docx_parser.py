from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.domain.enums import BlockType
from app.domain.schemas import SourceSpan
from app.storage.hashing import sha256_text


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    file_name: str
    spans: list[SourceSpan]
    paragraphs: list[SourceSpan]
    table_cells: list[SourceSpan]


class DocxParser:
    """Extract traceable text spans from body paragraphs and tables in XML order."""

    def parse(self, path: Path, document_id: str | None = None) -> ParsedDocument:
        path = Path(path)
        resolved_document_id = document_id or path.stem
        document = Document(path)

        spans: list[SourceSpan] = []
        paragraphs: list[SourceSpan] = []
        table_cells: list[SourceSpan] = []
        section_path: list[str] = []
        paragraph_index = 0
        table_index = 0

        for child in document.element.body.iterchildren():
            if child.tag.endswith("}p"):
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if text:
                    is_heading, heading_level = _heading_details(paragraph)
                    if is_heading:
                        section_path = section_path[: heading_level - 1] + [text]
                    span = SourceSpan(
                        span_id=f"{resolved_document_id}:p:{paragraph_index}",
                        document_id=resolved_document_id,
                        section_path=list(section_path),
                        block_type=(
                            BlockType.HEADING if is_heading else BlockType.PARAGRAPH
                        ),
                        paragraph_index=paragraph_index,
                        text=text,
                        text_hash=sha256_text(text),
                    )
                    spans.append(span)
                    paragraphs.append(span)
                paragraph_index += 1
            elif child.tag.endswith("}tbl"):
                table = Table(child, document)
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row.cells):
                        text = cell.text.strip()
                        if not text:
                            continue
                        span = SourceSpan(
                            span_id=(
                                f"{resolved_document_id}:t:{table_index}:"
                                f"{row_index}:{column_index}"
                            ),
                            document_id=resolved_document_id,
                            section_path=list(section_path),
                            block_type=BlockType.TABLE_CELL,
                            table_index=table_index,
                            row_index=row_index,
                            column_index=column_index,
                            text=text,
                            text_hash=sha256_text(text),
                        )
                        spans.append(span)
                        table_cells.append(span)
                table_index += 1

        return ParsedDocument(
            document_id=resolved_document_id,
            file_name=path.name,
            spans=spans,
            paragraphs=paragraphs,
            table_cells=table_cells,
        )


def _heading_details(paragraph: Paragraph) -> tuple[bool, int]:
    style_name = paragraph.style.name or ""
    match = re.search(r"(?:heading|标题)\s*(\d+)", style_name, flags=re.IGNORECASE)
    if match is None:
        return False, 1

    return True, min(max(int(match.group(1)), 1), 9)
