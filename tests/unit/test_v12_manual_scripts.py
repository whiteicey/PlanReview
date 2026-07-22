from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_manual_validation_scripts_are_isolated_and_loopback_only():
    start = (ROOT / "scripts" / "start_v12_manual_validation.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "scripts" / "stop_v12_manual_validation.ps1").read_text(encoding="utf-8")
    assert "v1_2_manual_validation\\storage" in start
    assert "127.0.0.1" in start
    assert "--port" in start and '"$Port"' in start
    assert "PythonExecutable" in start
    assert "PLANREVIEW_PYTHON" in start
    assert "import uvicorn" in start
    assert "secrets_included = $false" in start
    assert "uvicorn" in stop and "--port\\s+8877" in stop
    assert "defect_manifest" not in start
    assert "review.db" in start
