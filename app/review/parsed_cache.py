"""Bounded in-process cache for already validated DOCX parse results."""

from __future__ import annotations

from collections import OrderedDict
from threading import RLock

from app.parsers.docx_parser import ParsedDocument


class ParsedDocumentCache:
    """Keep sensitive span text in memory only and reuse upload-time parsing."""

    def __init__(self, max_cases: int = 8) -> None:
        if max_cases < 1:
            raise ValueError("max_cases must be positive")
        self.max_cases = max_cases
        self._items: OrderedDict[tuple[str, str], tuple[ParsedDocument, ...]] = OrderedDict()
        self._lock = RLock()

    def put(self, case_id: str, file_hash: str, documents: list[ParsedDocument]) -> None:
        key = (case_id, file_hash)
        with self._lock:
            self._items[key] = tuple(documents)
            self._items.move_to_end(key)
            while len(self._items) > self.max_cases:
                self._items.popitem(last=False)

    def get(self, case_id: str, file_hash: str) -> list[ParsedDocument] | None:
        key = (case_id, file_hash)
        with self._lock:
            value = self._items.get(key)
            if value is None:
                return None
            self._items.move_to_end(key)
            return list(value)

    def discard_case(self, case_id: str) -> None:
        with self._lock:
            for key in [key for key in self._items if key[0] == case_id]:
                self._items.pop(key, None)

