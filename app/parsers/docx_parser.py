from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.domain.enums import BlockType
from app.domain.exceptions import DocxResourceLimitError, ParseError, UnsafeDocxPackageError
from app.domain.schemas import SourceSpan
from app.storage.hashing import sha256_text
from app.storage.case_files import validate_docx_package
from app.settings import Settings, get_settings


@dataclass(frozen=True)
class ParsedDocument:
    document_id: str
    file_name: str
    spans: list[SourceSpan]
    paragraphs: list[SourceSpan]
    table_cells: list[SourceSpan]


class DocxParser:
    """Extract traceable text spans from body paragraphs and tables in XML order."""

    def __init__(self, limits: Settings | None = None) -> None:
        self.limits = limits or get_settings()

    def parse(self, path: Path, document_id: str | None = None) -> ParsedDocument:
        path = Path(path)
        resolved_document_id = document_id or path.stem
        try:
            validate_docx_package(path, self.limits)
            _preflight_document_xml(path, self.limits)
        except DocxResourceLimitError:
            raise
        except UnsafeDocxPackageError as exc:
            raise ParseError("Unable to parse DOCX document") from exc
        try:
            document = Document(path)
        except Exception as exc:
            raise ParseError("Unable to parse DOCX document") from exc

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
                seen_xml_cells: set[object] = set()
                for row_index, row in enumerate(table.rows):
                    for column_index, cell in enumerate(row.cells):
                        xml_cell = cell._tc
                        if xml_cell in seen_xml_cells:
                            continue
                        # Retain the XML object itself. Storing only id(xml_cell)
                        # permits Python proxy IDs to be reused during iteration.
                        seen_xml_cells.add(xml_cell)
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


def _preflight_document_xml(path: Path, limits: Settings) -> None:
    """Bound XML work before python-docx constructs its complete object graph."""
    characters = paragraphs = tables = cells = 0
    try:
        with ZipFile(path) as archive, archive.open("word/document.xml") as source:
            for _event, element in ElementTree.iterparse(source, events=("end",)):
                local_name = element.tag.rsplit("}", 1)[-1]
                if local_name == "t" and element.text:
                    characters += len(element.text)
                    if characters > limits.max_document_characters:
                        raise DocxResourceLimitError("DOCX document exceeds configured limits")
                elif local_name == "p":
                    paragraphs += 1
                    if paragraphs > limits.max_paragraphs:
                        raise DocxResourceLimitError("DOCX document exceeds configured limits")
                elif local_name == "tbl":
                    tables += 1
                    if tables > limits.max_tables:
                        raise DocxResourceLimitError("DOCX document exceeds configured limits")
                elif local_name == "tc":
                    cells += 1
                    if cells > limits.max_table_cells:
                        raise DocxResourceLimitError("DOCX document exceeds configured limits")
                element.clear()
    except DocxResourceLimitError:
        raise
    except (BadZipFile, KeyError, OSError, ElementTree.ParseError) as exc:
        raise UnsafeDocxPackageError("DOCX package structure is not supported") from exc


def _heading_details(paragraph: Paragraph) -> tuple[bool, int]:
    style_name = paragraph.style.name or ""
    match = re.search(r"(?:heading|标题)\s*(\d+)", style_name, flags=re.IGNORECASE)
    if match is None:
        return False, 1

    return True, min(max(int(match.group(1)), 1), 9)
