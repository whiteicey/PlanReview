"""Bounded, failure-tolerant lifecycle ledgers for V1.2 observability.

The ledgers deliberately retain only structured identifiers and decision metadata.
They never retain provider request/response bodies or report prose.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import logging
import os
from typing import Any

LOGGER = logging.getLogger(__name__)
LEDGER_SCHEMA_VERSION = "v1"
DEFAULT_LEDGER_MAX_ENTRIES = 10_000
DEFAULT_LEDGER_MAX_BYTES = 2 * 1024 * 1024


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_safe_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _safe_scalar(item) for key, item in value.items()}
    return str(value)


@dataclass
class LifecycleLedger:
    """A bounded append-only detail ledger with aggregate fallback."""

    name: str
    max_entries: int = DEFAULT_LEDGER_MAX_ENTRIES
    max_bytes: int = DEFAULT_LEDGER_MAX_BYTES
    entries: list[dict[str, Any]] = field(default_factory=list)
    summary: Counter[str] = field(default_factory=Counter)
    ledger_truncated: bool = False
    _size_bytes: int = 0

    def __post_init__(self) -> None:
        self.max_entries = max(0, int(self.max_entries))
        self.max_bytes = max(0, int(self.max_bytes))

    def append(self, entry: dict[str, Any], *, summary_keys: tuple[str, ...] = ()) -> bool:
        cleaned = {str(key): _safe_scalar(value) for key, value in entry.items()}
        for key in summary_keys:
            value = cleaned.get(key)
            if value is not None:
                self.summary[f"{key}:{value}"] += 1
        encoded = json.dumps(cleaned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        separator_bytes = 1 if self.entries else 0
        if (
            self.ledger_truncated
            or len(self.entries) >= self.max_entries
            or self._size_bytes + len(encoded) + separator_bytes > self.max_bytes
        ):
            if not self.ledger_truncated:
                LOGGER.warning(
                    "%s lifecycle ledger reached its configured limit; retaining summaries only",
                    self.name,
                )
            self.ledger_truncated = True
            return False
        self.entries.append(cleaned)
        self._size_bytes += len(encoded) + separator_bytes
        return True

    def extend(self, entries: list[dict[str, Any]], *, summary_keys: tuple[str, ...] = ()) -> None:
        for entry in entries:
            self.append(entry, summary_keys=summary_keys)

    def to_dict(self) -> dict[str, Any]:
        summary = dict(sorted(self.summary.items()))
        return {
            "ledger_schema_version": LEDGER_SCHEMA_VERSION,
            "ledger_entry_count": len(self.entries),
            "ledger_truncated": self.ledger_truncated,
            "ledger_size_bytes": self._size_bytes,
            "entries": list(self.entries),
            "summary": summary,
        }

    @classmethod
    def from_dict(
        cls,
        name: str,
        value: dict[str, Any] | None,
        *,
        max_entries: int = DEFAULT_LEDGER_MAX_ENTRIES,
        max_bytes: int = DEFAULT_LEDGER_MAX_BYTES,
    ) -> "LifecycleLedger":
        ledger = cls(name, max_entries=max_entries, max_bytes=max_bytes)
        if not isinstance(value, dict):
            return ledger
        entries = value.get("entries")
        if isinstance(entries, list):
            ledger.extend([item for item in entries if isinstance(item, dict)])
        ledger.ledger_truncated = bool(value.get("ledger_truncated", False))
        summary = value.get("summary")
        if isinstance(summary, dict):
            ledger.summary.update({str(k): int(v) for k, v in summary.items() if isinstance(v, int)})
        return ledger


def empty_ledger(name: str) -> dict[str, Any]:
    return LifecycleLedger(name).to_dict()


def configured_ledger(name: str) -> LifecycleLedger:
    """Build a ledger from bounded, non-secret environment configuration."""
    def _int_env(key: str, default: int) -> int:
        value = os.environ.get(key)
        try:
            parsed = int(value) if value is not None else default
        except (TypeError, ValueError):
            parsed = default
        return max(0, parsed)

    prefix = "REVIEW_PACKET_LEDGER" if name == "packet_lifecycle" else "REVIEW_AI_CANDIDATE_LEDGER"
    return LifecycleLedger(
        name,
        max_entries=_int_env(f"{prefix}_MAX_ENTRIES", DEFAULT_LEDGER_MAX_ENTRIES),
        max_bytes=_int_env(f"{prefix}_MAX_BYTES", DEFAULT_LEDGER_MAX_BYTES),
    )
