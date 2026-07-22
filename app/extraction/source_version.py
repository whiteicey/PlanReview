"""Conservative source-version parsing for document names."""

from __future__ import annotations

from pathlib import PurePath
import re

_V_VERSION = re.compile(r"(?<![A-Za-z0-9])v\s*(\d+(?:\.\d+)*)(?![\d.])", re.IGNORECASE)
_CHINESE_VERSION = re.compile(r"第\s*(\d+(?:\.\d+)*)\s*版")


def parse_source_version(value: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    candidate = PurePath(value).stem
    match = _V_VERSION.search(candidate) or _CHINESE_VERSION.search(candidate)
    return f"V{match.group(1)}" if match is not None else None
