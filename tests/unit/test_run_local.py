from __future__ import annotations

import importlib.util
import os
from pathlib import Path


RUN_LOCAL = Path(__file__).resolve().parents[2] / "scripts" / "run_local.py"


def _module():
    spec = importlib.util.spec_from_file_location("planreview_run_local", RUN_LOCAL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_shareable_local_launcher_enables_verified_v12_rules_without_overriding_operator_choice(monkeypatch):
    module = _module()
    explicit_false = "REVIEW_RULE_REFERENCE_001_ENABLED"
    monkeypatch.setenv(explicit_false, "false")
    monkeypatch.delenv("REVIEW_DEMO_ROOT", raising=False)
    for rule_id in module._VERIFIED_V12_RULE_IDS:
        if rule_id != "REFERENCE-001":
            monkeypatch.delenv(f"REVIEW_RULE_{rule_id.replace('-', '_')}_ENABLED", raising=False)

    module.enable_verified_v12_rules()

    assert os.environ[explicit_false] == "false"
    for rule_id in module._VERIFIED_V12_RULE_IDS:
        flag = f"REVIEW_RULE_{rule_id.replace('-', '_')}_ENABLED"
        if rule_id != "REFERENCE-001":
            assert os.environ[flag] == "true"
    assert Path(os.environ["REVIEW_DEMO_ROOT"]) == module._BUNDLED_DEMO_ROOT



def test_shareable_local_launcher_loads_the_complete_18_rule_bundle(monkeypatch):
    module = _module()
    monkeypatch.delenv("REVIEW_DEMO_ROOT", raising=False)
    for rule_id in module._VERIFIED_V12_RULE_IDS:
        monkeypatch.delenv(f"REVIEW_RULE_{rule_id.replace('-', '_')}_ENABLED", raising=False)

    module.enable_verified_v12_rules()

    from app.rules.ruleset import load_active_ruleset

    rules = load_active_ruleset().rules
    assert len(rules) == 18
    assert len({rule.rule_id for rule in rules if rule.enabled}) == 18
