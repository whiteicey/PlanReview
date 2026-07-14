from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"


def _load_import_demo():
    path = SCRIPTS / "import_demo.py"
    spec = importlib.util.spec_from_file_location("import_demo_contract", path)
    if spec is None or spec.loader is None:
        raise AssertionError("unable to load import_demo.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _external_demo_root(module) -> Path:
    try:
        return module.resolve_demo_root()
    except module.DemoRootNotFound as exc:
        pytest.skip(f"external DEMO package unavailable: {exc}")


def test_run_script_is_loopback_only():
    source = (SCRIPTS / "run_local.py").read_text(encoding="utf-8")
    assert 'host="127.0.0.1"' in source
    assert "0.0.0.0" not in source


def test_demo_docs_state_docx_scope():
    text = (ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")
    assert "仅处理文本型 DOCX" in text
    assert "DEMO_ONLY" in text


def test_demo_root_uses_only_explicit_resolution(monkeypatch, tmp_path: Path):
    module = _load_import_demo()
    monkeypatch.delenv("REVIEW_DEMO_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    with pytest.raises(module.DemoRootNotFound, match="REVIEW_DEMO_ROOT"):
        module.resolve_demo_root()


def test_external_demo_fixture_is_honest_or_skipped(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    assert root.is_dir()
    assert (root / "rules" / "ruleset-demo-0.1.yaml").is_file()
    assert (root / "rules" / "terminology-demo-0.1.yaml").is_file()


def test_import_demo_valid_package_uses_production_loaders_without_copy(monkeypatch, tmp_path: Path):
    module = _load_import_demo()
    root = _external_demo_root(module)
    plans = root / "plans"
    docx_candidates = sorted(plans.glob("*.docx"))
    if not docx_candidates:
        pytest.skip(f"external DEMO package has no DOCX fixture under {plans}")
    source = docx_candidates[0]
    storage = tmp_path / "storage"
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))

    imported = module.import_demo(source, storage_root=storage)

    assert imported.source_docx == source.resolve()
    assert imported.rules
    assert all(rule.source_type == "DEMO_ONLY" for rule in imported.rules)
    assert imported.terminology.canonicalize("部署井数") == "开发井总数"
    assert not storage.exists()


def test_import_demo_normalizes_legacy_rule_params(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))
    source = sorted((root / "plans").glob("DEMO-002*.docx"))[0]

    imported = module.import_demo(source)
    by_id = {rule.rule_id: rule for rule in imported.rules}

    assert by_id["CONSISTENCY-001"].params["parameter"] == "开发井总数"
    assert by_id["VERSION-001"].params["reason_terms"] == ["调整原因", "变更说明", "依据", "审查意见", "复核"]
    assert by_id["VERSION-001"].params["parameters"]
    assert by_id["VERSION-002"].params["status_terms"] == ["待整改", "整改中", "已整改", "已闭环"]
    assert by_id["EVIDENCE-001"].params["min_evidence"] == 1
    assert "legacy_match_dimensions" in by_id["CAPACITY-001"].params


def test_import_demo_rejects_non_docx_and_missing_files(tmp_path: Path):
    module = _load_import_demo()
    with pytest.raises(module.DemoImportError, match="DOCX"):
        module.validate_docx(tmp_path / "sample.pdf")
    with pytest.raises(module.DemoImportError, match="不存在"):
        module.validate_docx(tmp_path / "missing.docx")


def test_import_demo_accepts_established_top_level_schemas(monkeypatch, tmp_path: Path):
    module = _load_import_demo()
    demo_root = tmp_path / "本地版示例数据包"
    rules = demo_root / "rules"
    rules.mkdir(parents=True)
    (rules / "ruleset-demo-0.1.yaml").write_text(
        "rules:\n"
        "  - rule_id: R1\n"
        "    version: '0.1'\n"
        "    name: test\n"
        "    category: completeness\n"
        "    severity: medium\n"
        "    operator: all_equal\n"
        "    on_missing: unknown\n"
        "    source_type: DEMO_ONLY\n",
        encoding="utf-8",
    )
    (rules / "terminology-demo-0.1.yaml").write_text(
        "aliases:\n  开发井总数: [部署井数]\n", encoding="utf-8"
    )
    source = tmp_path / "outside" / "sample.docx"
    source.parent.mkdir()
    source.write_bytes(b"not parsed here")
    storage = tmp_path / "storage"
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(demo_root))

    imported = module.import_demo(source, storage_root=storage)

    assert imported.rules[0].rule_id == "R1"
    assert imported.terminology.canonicalize("部署井数") == "开发井总数"
    assert not storage.exists()
