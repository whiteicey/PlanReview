from pathlib import Path

from app.extraction.sections import extract_sections
from app.parsers.docx_parser import DocxParser
from .test_docx_parser import make_docx


def test_extract_sections_groups_spans(tmp_path: Path) -> None:
    path = tmp_path / "minimal.docx"
    make_docx(path)

    parsed = DocxParser().parse(path, "D1")
    sections = extract_sections(parsed)

    assert sections[0].title == "一、项目概况"
    assert sections[0].path == ["一、项目概况"]
    assert sections[0].span_ids
    assert any(s.text == "本项目位于测试区。" for s in parsed.paragraphs)
    assert all(span_id in {span.span_id for span in parsed.spans} for span_id in sections[0].span_ids)
