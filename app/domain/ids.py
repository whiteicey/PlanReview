"""Stable identifiers for review runs."""

from __future__ import annotations

from uuid import UUID, uuid4, uuid5


# Permanent namespace: changing this would change every migrated legacy run ID.
LEGACY_REVIEW_RUN_NAMESPACE = UUID("6f4f4cc8-6a6d-4b19-8c80-0f1e8c1c0a76")


def new_review_run_id() -> str:
    return str(uuid4())


def legacy_review_run_id(case_id: str) -> str:
    return str(uuid5(LEGACY_REVIEW_RUN_NAMESPACE, f"review-run:{case_id}"))


def normalize_review_run_id(value: str) -> str:
    """Require the canonical string representation of a UUID."""

    parsed = UUID(value)
    normalized = str(parsed)
    if value.casefold() != normalized:
        raise ValueError("run_id must be a canonical UUID")
    return normalized
