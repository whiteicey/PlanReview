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
    rf"(?P<unit>万m³/d|万m3/d|m³/d|m3/d|万吨/年|个月|口|座|%)?"
)


def _number(value: str) -> float | None:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def _header_columns(row: dict[int, SourceSpan]) -> dict[str, int]:
    """Map normalized table headers to their columns without changing cell data."""
    return {cell.text.strip(): column_index for column_index, cell in row.items()}


def _cell_text(row: dict[int, SourceSpan], headers: dict[str, int], *labels: str) -> str | None:
    for label in labels:
        column_index = headers.get(label)
        if column_index is not None and column_index in row:
            return row[column_index].text.strip()
    return None


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
                if "参数名称" in "".join(cell.text for cell in row.values())
            ),
            None,
        )
        if header_row_index is None:
            continue

        headers = _header_columns(rows[header_row_index])
        name_column = headers.get("参数名称")
        value_column = headers.get("数值")
        if name_column is None or value_column is None:
            continue

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
                    raw_unit=_cell_text(row, headers, "单位"),
                    subject=_cell_text(row, headers, "对象"),
                    time_scope=_cell_text(row, headers, "时间/阶段", "时间", "阶段"),
                    statistical_scope=_cell_text(row, headers, "统计口径"),
                    condition=_cell_text(row, headers, "条件"),
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
