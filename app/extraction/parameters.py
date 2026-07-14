from __future__ import annotations

import re

from app.domain.enums import ExtractionMethod
from app.domain.schemas import ParameterFact, SourceSpan
from app.parsers.docx_parser import ParsedDocument


_NUMBER = r"[-+]?\d[\d,]*(?:\.\d+)?"
# Keep the unit vocabulary deliberately explicit. Table units are always retained
# verbatim; body extraction only claims units it can identify unambiguously.
_BODY = re.compile(
    rf"(?P<name>[一-鿿A-Za-z0-9/（）()]+?)(?:为|：|:)\s*"
    rf"(?P<value>{_NUMBER})\s*"
    # A complete supported unit is required. An optional unit would turn a
    # date such as ``投产时间：2028年03月`` into a false 2028 fact.
    rf"(?P<unit>万m³/d|万m3/d|m³/d|m3/d|万吨/年|个月|口|座|%)"
)

_HEADER_ALIASES = {
    "name": {"参数名称", "名称"},
    "value": {"数值"},
    "unit": {"单位"},
    "subject": {"对象"},
    "time": {"时间"},
    "stage": {"阶段"},
    "time_stage": {"时间/阶段"},
    "statistical": {"统计口径"},
    "condition": {"条件"},
}


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _normalized_header(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _canonical_header(text: str) -> str | None:
    normalized = _normalized_header(text)
    for canonical, aliases in _HEADER_ALIASES.items():
        if normalized in {_normalized_header(alias) for alias in aliases}:
            return canonical
    return None


def _header_columns(
    rows: dict[int, dict[int, SourceSpan]], header_end: int
) -> dict[str, list[int]]:
    """Flatten header rows, including DOCX-expanded merged cells.

    A merged cell is repeated by python-docx in each covered column, so each
    column receives all non-empty labels from the header block. Labels are
    normalized only for matching; cell text and source spans remain untouched.
    """
    header_columns: dict[str, list[int]] = {}
    for row_index in range(header_end + 1):
        for column_index, cell in rows.get(row_index, {}).items():
            canonical = _canonical_header(cell.text)
            if canonical is None:
                continue
            header_columns.setdefault(canonical, [])
            if column_index not in header_columns[canonical]:
                header_columns[canonical].append(column_index)
    return header_columns


def _columns_for(headers: dict[str, list[int]], *labels: str) -> list[int]:
    columns: list[int] = []
    for label in labels:
        for column_index in headers.get(label, []):
            if column_index not in columns:
                columns.append(column_index)
    return columns


def _cell_text(
    row: dict[int, SourceSpan], headers: dict[str, list[int]], *labels: str
) -> str | None:
    for column_index in _columns_for(headers, *labels):
        if column_index in row:
            return row[column_index].text.strip()
    return None


def _combined_time_scope(
    row: dict[int, SourceSpan], headers: dict[str, list[int]]
) -> str | None:
    combined: list[str] = []
    for label, display_label in (("time", "时间"), ("stage", "阶段")):
        value = _cell_text(row, headers, label)
        if value is not None:
            combined.append(f"{display_label}={value}")
    if combined:
        return ";".join(combined)
    return _cell_text(row, headers, "time_stage")


def _table_facts(parsed: ParsedDocument, source_version: str | None) -> list[ParameterFact]:
    facts: list[ParameterFact] = []
    table_indexes = sorted(
        {cell.table_index for cell in parsed.table_cells if cell.table_index is not None}
    )

    for table_index in table_indexes:
        rows: dict[int, dict[int, SourceSpan]] = {}
        for cell in parsed.table_cells:
            if cell.table_index == table_index:
                rows.setdefault(cell.row_index, {})[cell.column_index] = cell

        header_row_index = next(
            (
                row_index
                for row_index, row in rows.items()
                if {"name", "value"}.issubset(
                    {
                        canonical
                        for canonical in (_canonical_header(cell.text) for cell in row.values())
                        if canonical is not None
                    }
                )
            ),
            None,
        )
        if header_row_index is None:
            continue

        headers = _header_columns(rows, header_row_index)
        name_columns = _columns_for(headers, "name")
        value_columns = _columns_for(headers, "value")
        if not name_columns or not value_columns:
            continue
        name_column, value_column = name_columns[0], value_columns[0]

        for row_index, row in rows.items():
            if row_index <= header_row_index:
                continue
            name_cell = row.get(name_column)
            value_cell = row.get(value_column)
            if name_cell is None or value_cell is None:
                continue

            raw_name = name_cell.text.strip()
            raw_value = value_cell.text.strip()
            if not raw_name or not raw_value:
                continue

            facts.append(
                ParameterFact(
                    fact_id=f"{parsed.document_id}:fact:{len(facts)}",
                    canonical_name=raw_name,
                    raw_name=raw_name,
                    raw_value=raw_value,
                    normalized_value=_number(raw_value),
                    raw_unit=_cell_text(row, headers, "unit"),
                    subject=_cell_text(row, headers, "subject"),
                    time_scope=_combined_time_scope(row, headers),
                    statistical_scope=_cell_text(row, headers, "statistical"),
                    condition=_cell_text(row, headers, "condition"),
                    source_document=parsed.document_id,
                    source_version=source_version,
                    source_span_id=value_cell.span_id,
                    extraction_method=ExtractionMethod.TABLE,
                )
            )

    return facts


def _body_facts(
    parsed: ParsedDocument, source_version: str | None, start_index: int
) -> list[ParameterFact]:
    facts: list[ParameterFact] = []
    for span in parsed.paragraphs:
        for match in _BODY.finditer(span.text):
            raw_name = match.group("name")
            raw_value = match.group("value")
            facts.append(
                ParameterFact(
                    fact_id=f"{parsed.document_id}:fact:{start_index + len(facts)}",
                    canonical_name=raw_name,
                    raw_name=raw_name,
                    raw_value=raw_value,
                    normalized_value=_number(raw_value),
                    raw_unit=match.group("unit"),
                    source_document=parsed.document_id,
                    source_version=source_version,
                    source_span_id=span.span_id,
                    extraction_method=ExtractionMethod.REGEX,
                )
            )
    return facts


def extract_parameter_facts(
    parsed: ParsedDocument, source_version: str | None = None
) -> list[ParameterFact]:
    """Extract independent, source-traceable parameter occurrences.

    Facts are intentionally not deduplicated: equal or conflicting values in a
    table and in prose are separate evidence with their original source spans.
    Missing comparison dimensions remain ``None`` rather than being inferred.
    """
    table_facts = _table_facts(parsed, source_version)
    return table_facts + _body_facts(parsed, source_version, len(table_facts))
