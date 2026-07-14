from __future__ import annotations

import importlib.util
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


def test_import_demo_reads_assets_without_copying_docx(monkeypatch, tmp_path: Path):
    module = _load_import_demo()
    demo_root = tmp_path / "本地版示例数据包"
    rules = demo_root / "rules"
    rules.mkdir(parents=True)
    (rules / "ruleset-demo-0.1.yaml").write_text(
        "metadata:\n  source_type: DEMO_ONLY\nrules: []\n", encoding="utf-8"
    )
    (rules / "terminology-demo-0.1.yaml").write_text(
        "metadata:\n  source_type: DEMO_ONLY\naliases: {}\n", encoding="utf-8"
    )
    source = tmp_path / "outside" / "sample.docx"
    source.parent.mkdir()
    source.write_bytes(b"not parsed here")
    storage = tmp_path / "storage"
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(demo_root))

    imported = module.import_demo(source, storage_root=storage)

    assert imported.source_docx == source.resolve()
    assert imported.rules["metadata"]["source_type"] == "DEMO_ONLY"
    assert imported.terminology["aliases"] == {}
    assert not storage.exists()


def test_import_demo_rejects_non_docx_and_missing_files(tmp_path: Path):
    module = _load_import_demo()
    with pytest.raises(module.DemoImportError, match="DOCX"):
        module.validate_docx(tmp_path / "sample.pdf")
    with pytest.raises(module.DemoImportError, match="不存在"):
        module.validate_docx(tmp_path / "missing.docx")
