"""Independent V1.2 rule feature flags.

Flags default to disabled and are opt-in through explicit environment values;
there is no test-file, manifest, or coordinate-specific path.
"""

from __future__ import annotations

import os


V12_RULE_IDS = (
    "REFERENCE-001",
    "SUMMARY_DETAIL-001",
    "CROSS_SOURCE_PARAM-001",
    "UNIT_MAGNITUDE-001",
    "SCHEDULE-001",
    "EQUIPMENT_REDUNDANCY-001",
)


def feature_flag_name(rule_id: str) -> str:
    return f"REVIEW_RULE_{rule_id.replace('-', '_')}_ENABLED"


def is_rule_enabled(rule_id: str, declared_enabled: bool) -> bool:
    if rule_id not in V12_RULE_IDS:
        return bool(declared_enabled)
    value = os.environ.get(feature_flag_name(rule_id))
    if value is None:
        return False
    return value.strip().casefold() in {"1", "true", "yes", "on", "enabled"}
