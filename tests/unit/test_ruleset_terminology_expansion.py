from __future__ import annotations

import yaml

from app.extraction.terminology import TerminologyMap
from app.rules.ruleset import load_production_rules


def test_alias_rule_expands_every_terminology_entry(tmp_path) -> None:
    path = tmp_path / "rules.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "rules": [
                    {
                        "rule_id": "TERM-001",
                        "version": "1",
                        "name": "术语统一",
                        "category": "terminology",
                        "severity": "high",
                        "operator": "alias_normalization",
                        "on_missing": "unknown",
                        "source_type": "DEMO_ONLY",
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    terminology = TerminologyMap.from_mapping(
        {"开发井总数": ["钻井总数"], "处理能力": ["处理量"]}
    )

    rules = load_production_rules(path, terminology)

    assert len(rules) == 1
    assert rules[0].params["parameters"] == ["开发井总数", "处理能力"]
    assert rules[0].params["aliases_by_parameter"] == {
        "开发井总数": ["钻井总数"],
        "处理能力": ["处理量"],
    }
