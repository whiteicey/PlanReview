"""Start the local demo server on loopback only."""

import os
from pathlib import Path

import uvicorn

from app.security.loopback import assert_loopback_host


_VERIFIED_V12_RULE_IDS = (
    "REFERENCE-001",
    "SUMMARY_DETAIL-001",
    "CROSS_SOURCE_PARAM-001",
    "UNIT_MAGNITUDE-001",
    "SCHEDULE-001",
    "EQUIPMENT_REDUNDANCY-001",
)
_BUNDLED_DEMO_ROOT = Path(__file__).resolve().parents[1] / "本地版示例数据包"


def enable_verified_v12_rules() -> None:
    """Enable the verified V1.2 capability set for normal local launches.

    The repository YAML keeps these feature flags off by default so operators
    can independently disable a capability.  The shareable local launcher is
    the approved complete V1.2 mode, while an explicit environment value still
    wins (for example, ``REVIEW_RULE_REFERENCE_001_ENABLED=false``).
    """
    for rule_id in _VERIFIED_V12_RULE_IDS:
        flag = f"REVIEW_RULE_{rule_id.replace('-', '_')}_ENABLED"
        os.environ.setdefault(flag, "true")
    # The shareable package includes only this safe rules/terminology subset of
    # the DEMO_ONLY assets.  A caller can still select another approved root.
    if _BUNDLED_DEMO_ROOT.is_dir():
        os.environ.setdefault("REVIEW_DEMO_ROOT", str(_BUNDLED_DEMO_ROOT))


if __name__ == "__main__":  # pragma: no cover - manual startup
    enable_verified_v12_rules()
    host = assert_loopback_host("127.0.0.1")
    uvicorn.run("app.main:app", host=host, port=8765, reload=False)
