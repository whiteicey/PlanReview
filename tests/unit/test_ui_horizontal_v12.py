from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


def test_horizontal_ui_assets_exist_and_load_once_in_dependency_order():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="/([^\"]+)"', html)
    expected = [
        "layout.js",
        "review_state.js",
        "workbench_state.js",
        "review_display_queue.js",
        "review_progress.js",
        "expert_experience.js",
        "app.js",
    ]
    assert scripts == expected
    assert len(scripts) == len(set(scripts))
    assert all((WEB / name).is_file() for name in scripts)
    assert (WEB / "styles.css").is_file()


def test_three_layout_modes_share_one_business_dom_and_one_progress_root():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    for element_id in ("upload", "result", "review-progress-root", "layout-mode"):
        assert html.count(f'id="{element_id}"') == 1
    assert 'value="auto"' in html
    assert 'value="desktop"' in html
    assert 'value="compact"' in html
    assert "data-slot=" not in html
    assert "component-bank" not in html


def test_v12_diagnostics_language_does_not_overclaim_stage_completion():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    adapter = (WEB / "workbench_state.js").read_text(encoding="utf-8")
    assert "AI有效候选" in html
    assert "阶段记录" in app
    assert "8个阶段全部完成" not in html + app + adapter
    assert "allStagesCompleted: null" in adapter


def test_layout_does_not_create_a_second_polling_or_run_controller():
    layout = (WEB / "layout.js").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "/api/runs/" not in layout
    assert "review-jobs" not in layout
    assert app.count("createReviewProgressController") == 1


def test_ruleset_status_separates_library_total_from_real_run_execution_count():
    app = (WEB / "app.js").read_text(encoding="utf-8")
    progress = (WEB / "review_progress.js").read_text(encoding="utf-8")
    assert "规则库共${total}条，当前启用${enabled}" in app
    assert "diagnosticsView.distinctRuleIdCount" in app
    assert '`${metric(metrics.completed_rule_count)}/${metric(metrics.applicable_rule_count)}`' in progress
    assert "已加载 ${status.rule_count} 条审查规则" not in app


def test_ai_settings_actions_keep_visible_labels_and_a_consistent_button_row():
    styles = (WEB / "styles.css").read_text(encoding="utf-8")
    assert ".case-panel .llm-config-actions{display:grid" in styles
    assert ".case-panel .llm-config-actions #llm-save" in styles
    assert "color:#fff" in styles
    assert "white-space:nowrap" in styles


def test_finding_card_uses_actual_evidence_count_and_clear_absence_copy() -> None:
    app = (WEB / "app.js").read_text(encoding="utf-8")
    assert "Array.isArray(finding.evidence_span_ids)" in app
    assert "未检索到对应内容" in app
    assert "${evidenceCount} 处" in app


def test_expert_experience_ui_uses_live_api_counts_and_display_only_events() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    app = (WEB / "app.js").read_text(encoding="utf-8")
    queue = (WEB / "review_display_queue.js").read_text(encoding="utf-8")
    assert 'id="reload-expert-experiences"' in html
    assert 'id="load-expert-experiences"' in html
    assert 'id="expert-experience-status"' in html
    assert 'id="expert-experience-digest"' in html
    assert "/api/expert-experiences/digest?limit=8" in app
    assert "renderExpertExperienceDigest" in app
    assert "最近归纳" in app
    assert "expert_experience_total_count" in app
    assert "is_expert_experience" in app
    assert "正在加载专家经验库" in app
    assert "专家经验库加载完成" in app
    assert "buildDisplayOnlyItems" in queue
