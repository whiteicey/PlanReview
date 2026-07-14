from pathlib import Path

from app.domain.enums import BlockType
from app.parsers.docx_parser import DocxParser
from app.rules.selectors import select_spans
from .test_docx_parser import make_docx


def test_selector_filters_without_eval(tmp_path: Path) -> None:
    path = tmp_path / "minimal.docx"
    make_docx(path)

    parsed = DocxParser().parse(path, "D1")
    cells = select_spans(
        parsed,
        section_contains="项目概况",
        block_type=BlockType.TABLE_CELL,
    )

    assert any(s.text == "36" for s in cells)
    assert all(s.block_type is BlockType.TABLE_CELL for s in cells)
    assert all("项目概况" in s.section_path[0] for s in cells)


def test_selector_does_not_interpret_expression_like_text(tmp_path: Path) -> None:
    path = tmp_path / "minimal.docx"
    make_docx(path)

    parsed = DocxParser().parse(path, "D1")
    selected = select_spans(parsed, section_contains='__import__("os").getcwd()')

    assert selected == []
