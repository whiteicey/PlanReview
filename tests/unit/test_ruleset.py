from __future__ import annotations

from pathlib import Path

import pytest

from app.rules.ruleset import (
    RulesetNotConfigured,
    _first_bundle_with_sentinel,
    resolve_ruleset_root,
)


def _make_bundle(root: Path) -> Path:
    bundle = root / "本地版示例数据包"
    (bundle / "rules").mkdir(parents=True)
    (bundle / "rules" / "ruleset-demo-0.1.yaml").write_text("rules: []\n", encoding="utf-8")
    (bundle / "rules" / "terminology-demo-0.1.yaml").write_text("aliases: {}\n", encoding="utf-8")
    return bundle


def test_env_override_takes_priority(monkeypatch, tmp_path):
    bundle = _make_bundle(tmp_path)
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(bundle))
    assert resolve_ruleset_root() == bundle.resolve()


def test_env_override_missing_dir_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_DEMO_ROOT", str(tmp_path / "nope"))
    with pytest.raises(RulesetNotConfigured):
        resolve_ruleset_root()


def test_sentinel_helper_walks_ancestors_and_gates_on_ruleset_file(tmp_path):
    # The bundle sits several levels above the starting directory (worktree-like).
    bundle = _make_bundle(tmp_path)
    deep = tmp_path / "review" / ".claude" / "worktrees" / "kernel-implementation"
    deep.mkdir(parents=True)

    found = _first_bundle_with_sentinel([deep, *deep.parents])
    assert found == bundle.resolve()


def test_sentinel_helper_skips_same_named_dir_without_ruleset_file(tmp_path):
    # A directory named 本地版示例数据包 but lacking the ruleset file is ignored.
    (tmp_path / "本地版示例数据包").mkdir()
    start = tmp_path / "sub"
    start.mkdir()

    assert _first_bundle_with_sentinel([start, *start.parents]) is None


def test_resolve_discovers_bundle_from_cwd_ancestors(monkeypatch):
    # Without the env override, resolution walks ancestors of both the installed
    # package and the cwd. In this repo the real bundle lives above the worktree,
    # so resolution either succeeds (pointing at a dir holding the ruleset file)
    # or honestly reports it is not configured — never a wrong directory.
    monkeypatch.delenv("REVIEW_DEMO_ROOT", raising=False)
    try:
        resolved = resolve_ruleset_root()
    except RulesetNotConfigured:
        pytest.skip("真实示例包不在祖先目录中（当前环境）")
    assert (resolved / "rules" / "ruleset-demo-0.1.yaml").is_file()
