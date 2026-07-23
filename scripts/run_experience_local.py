"""Start the isolated expert-experience workbench on 127.0.0.1:8891."""

from __future__ import annotations

import os
from pathlib import Path
import sys

EXPERIENCE_ROOT = Path(__file__).resolve().parents[1]
if str(EXPERIENCE_ROOT) not in sys.path:
    sys.path.insert(0, str(EXPERIENCE_ROOT))

import uvicorn

from app.security.loopback import assert_loopback_host
try:
    from scripts.run_local import enable_verified_v12_rules
except ModuleNotFoundError:  # direct ``python scripts/run_experience_local.py`` launch
    from run_local import enable_verified_v12_rules

EXPERIENCE_STORAGE = EXPERIENCE_ROOT / "storage"


if __name__ == "__main__":  # pragma: no cover
    enable_verified_v12_rules()
    os.environ.setdefault("REVIEW_STORAGE_ROOT", str(EXPERIENCE_STORAGE))
    uvicorn.run("app.main:app", host=assert_loopback_host("127.0.0.1"), port=8891, reload=False)
