"""Shared unit metadata used by normalization and arithmetic rules."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UnitMetadata:
    """The physical meaning of a canonical fact unit.

    ``category`` is intentionally separate from ``dimension``.  It lets
    arithmetic rules recognize count operands without coupling them to one
    particular Chinese display unit (for example, ``口`` versus ``座``).
    """

    category: str
    dimension: str
    canonical_unit: str


UNIT_REGISTRY: dict[str, UnitMetadata] = {
    "m^3/day": UnitMetadata("flow", "volume_flow", "m^3/day"),
    "口": UnitMetadata("count", "count", "口"),
    # These aliases are registered now so extending extraction does not
    # require changing operator logic.
    "座": UnitMetadata("count", "count", "座"),
    "台": UnitMetadata("count", "count", "台"),
    "套": UnitMetadata("count", "count", "套"),
}


def get_unit_metadata(
    canonical_unit: str | None, unit_category: str | None
) -> UnitMetadata | None:
    """Return trusted metadata, requiring the persisted category.

    The category requirement deliberately makes legacy facts without
    ``unit_category`` fail closed in arithmetic operators.
    """

    if not isinstance(canonical_unit, str) or not canonical_unit:
        return None
    if not isinstance(unit_category, str) or not unit_category:
        return None
    metadata = UNIT_REGISTRY.get(canonical_unit)
    if metadata is None or metadata.category != unit_category:
        return None
    return metadata
