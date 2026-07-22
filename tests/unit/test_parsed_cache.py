from app.review.parsed_cache import ParsedDocumentCache
from app.parsers.docx_parser import ParsedDocument


def document(identifier: str) -> ParsedDocument:
    return ParsedDocument(identifier, f"{identifier}.docx", [], [], [])


def test_parsed_document_cache_reuses_and_evicts_lru():
    cache = ParsedDocumentCache(max_cases=2)
    cache.put("A", "1", [document("A")])
    cache.put("B", "2", [document("B")])
    assert cache.get("A", "1")[0].document_id == "A"
    cache.put("C", "3", [document("C")])
    assert cache.get("B", "2") is None
    assert cache.get("A", "1") is not None

