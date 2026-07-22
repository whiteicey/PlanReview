from __future__ import annotations

from pathlib import Path

import pytest

from app.domain.enums import OnMissing, Severity
from app.domain.exceptions import RuleLoadError
from app.rules.loader import load_rules, load_terminology


def write(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    return path


def test_loads_demo_rule_shape_and_model_defaults(tmp_path: Path) -> None:
    path = write(
        tmp_path / "rules.yaml",
        "rules:\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: test\n"
        "    category: completeness\n"
        "    severity: high\n"
        "    operator: required_sections_exist\n"
        "    on_missing: fail\n",
    )

    rules = load_rules(path)

    assert rules == [
        rules[0].model_copy(
            update={
                "rule_id": "R1",
                "severity": Severity.HIGH,
                "on_missing": OnMissing.FAIL,
            }
        )
    ]
    assert rules[0].enabled is True
    assert rules[0].params == {}
    assert rules[0].source_type == "DEMO_ONLY"


def test_rejects_non_demo_source_type(tmp_path: Path) -> None:
    path = write(
        tmp_path / "rules.yaml",
        "rules:\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: test\n"
        "    category: completeness\n"
        "    severity: medium\n"
        "    operator: all_equal\n"
        "    on_missing: unknown\n"
        "    source_type: POLICY\n",
    )

    with pytest.raises(RuleLoadError, match="source_type"):
        load_rules(path)


def test_retains_demo_source_type_and_optional_values(tmp_path: Path) -> None:
    path = write(
        tmp_path / "rules.yaml",
        "rules:\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: test\n"
        "    category: completeness\n"
        "    severity: medium\n"
        "    operator: all_equal\n"
        "    on_missing: unknown\n"
        "    enabled: false\n"
        "    params: {parameter: 开发井总数}\n"
        "    source_type: DEMO_ONLY\n",
    )

    rule = load_rules(path)[0]

    assert rule.enabled is False
    assert rule.params == {"parameter": "开发井总数"}
    assert rule.source_type == "DEMO_ONLY"


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("[]\n", "根节点"),
        ("aliases: {}\n", "缺少 rules"),
        ("rules: {}\n", "必须是列表"),
        ("rules:\n  - rule_id: R1\n", "version"),
        (
            "rules:\n"
            "  - rule_id: R1\n"
            "    version: '0.1'\n"
            "    name: test\n"
            "    category: completeness\n"
            "    severity: high\n"
            "    operator: all_equal\n"
            "    on_missing: unrecognized\n",
            "on_missing",
        ),
        (
            "rules:\n"
            "  - rule_id: R1\n"
            "    version: '0.1'\n"
            "    name: test\n"
            "    category: completeness\n"
            "    severity: high\n"
            "    operator: arbitrary_code\n"
            "    on_missing: unknown\n",
            "未知 operator",
        ),
    ],
)
def test_rejects_invalid_rule_documents(
    tmp_path: Path, contents: str, message: str
) -> None:
    with pytest.raises(RuleLoadError, match=message):
        load_rules(write(tmp_path / "rules.yaml", contents))


def test_rejects_duplicate_rule_id(tmp_path: Path) -> None:
    path = write(
        tmp_path / "rules.yaml",
        "rules:\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: a\n"
        "    category: other\n"
        "    severity: low\n"
        "    operator: all_equal\n"
        "    on_missing: unknown\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: b\n"
        "    category: other\n"
        "    severity: low\n"
        "    operator: all_equal\n"
        "    on_missing: unknown\n",
    )

    with pytest.raises(RuleLoadError, match="重复 rule_id: R1"):
        load_rules(path)


def test_loader_never_executes_yaml_tagged_strings(tmp_path: Path) -> None:
    marker = tmp_path / "executed"
    path = write(
        tmp_path / "malicious.yaml",
        "rules:\n"
        "  - !!python/object/apply:os.system\n"
        f"    - 'touch {marker.as_posix()}'\n",
    )

    with pytest.raises(RuleLoadError):
        load_rules(path)

    assert not marker.exists()


def test_loads_aliases(tmp_path: Path) -> None:
    path = write(
        tmp_path / "terms.yaml", "aliases:\n  开发井总数: [钻井总数, 部署井数]\n"
    )

    assert load_terminology(path).canonicalize("部署井数") == "开发井总数"


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("rules: []\n", "缺少 aliases"),
        ("aliases: []\n", "必须是对象"),
        ("aliases:\n  1: [部署井数]\n", "字符串"),
        ("aliases:\n  开发井总数: 部署井数\n", "列表"),
        ("aliases:\n  开发井总数: [部署井数, 1]\n", "字符串"),
    ],
)
def test_rejects_invalid_terminology_documents(
    tmp_path: Path, contents: str, message: str
) -> None:
    with pytest.raises(RuleLoadError, match=message):
        load_terminology(write(tmp_path / "terms.yaml", contents))
