from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from app.llm.mock import MockProvider
from app.parsers.docx_parser import DocxParser
from app.review.pipeline import ReviewPipeline


ROOT = Path(__file__).parents[2]
SCRIPTS = ROOT / "scripts"


def _load_import_demo():
    path = SCRIPTS / "import_demo.py"
    spec = importlib.util.spec_from_file_location("import_demo_repo_rules", path)
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


def test_import_demo_includes_repo_owned_generic_rules(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))
    source = sorted((root / "plans").glob("DEMO-004*.docx"))[0]

    imported = module.import_demo(source)
    by_id = {rule.rule_id: rule for rule in imported.rules}

    assert "COMPLETENESS-003" in by_id
    assert by_id["COMPLETENESS-003"].operator == "reply_table_status_complete"
    assert "TERM-002" in by_id
    assert by_id["TERM-002"].operator == "prose_alias_unnormalized"


def test_version_rule_carries_declarative_human_review(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))
    source = sorted((root / "plans").glob("DEMO-003*.docx"))[0]

    imported = module.import_demo(source)
    by_id = {rule.rule_id: rule for rule in imported.rules}

    assert by_id["VERSION-001"].requires_human_review is True


def test_term002_alias_terms_populated_from_terminology(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))
    source = sorted((root / "plans").glob("DEMO-004*.docx"))[0]

    imported = module.import_demo(source)
    term002 = {rule.rule_id: rule for rule in imported.rules}["TERM-002"]
    terms = term002.params["terms"]

    assert terms, "TERM-002 must receive alias terms from the terminology map"
    by_canonical = {entry["canonical"]: entry["aliases"] for entry in terms}
    assert "部署井数" in by_canonical["开发井总数"]
    # The canonical name itself must not be listed as one of its own aliases.
    assert "开发井总数" not in by_canonical["开发井总数"]


def test_demo001_baseline_yields_zero_findings(monkeypatch):
    module = _load_import_demo()
    root = _external_demo_root(module)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(root))
    source = sorted((root / "plans").glob("DEMO-001*.docx"))[0]

    imported = module.import_demo(source)
    document = DocxParser().parse(source, document_id="DEMO-001")
    run = ReviewPipeline(imported.terminology).run(
        "DEMO-001", [document], imported.rules, MockProvider()
    )

    assert run.findings == []
    assert all(result.status.value == "PASS" for result in run.rule_results), [
        (r.rule_id, r.parameter, r.status.value) for r in run.rule_results if r.status.value != "PASS"
    ]
