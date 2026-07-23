"use strict";

const layoutController = globalThis.createLayoutController();
layoutController.apply();

const result = document.querySelector("#result");
const resultComponent = document.querySelector("#result-component");
const progressRoot = document.querySelector("#review-progress-root");
const rulesetStatusEl = document.querySelector("#ruleset-status");
const expertExperienceStatusEl = document.querySelector("#expert-experience-status");
const loadExpertExperiencesEl = document.querySelector("#load-expert-experiences");
const expertExperienceDigestEl = document.querySelector("#expert-experience-digest-content");
const experienceLibraryEl = document.querySelector("#expert-experience-library");
const experienceLibraryListEl = document.querySelector('[data-role="experience-library-list"]');
const EXPERT_EXPERIENCE_PREFERENCE_KEY = "planreview.loadExpertExperiences.v1";
const EXPERT_EXPERIENCE_RUNS_KEY = "planreview.expertExperienceRuns.v1";
let currentCaseId = null;
let currentRunId = null;
let currentSummary = null;
let currentFindings = [];
let rulesetDefinitionCount = null;
let currentRunDistinctRuleCount = null;
let expertExperienceSummary = { total_count: 0, updated_at: null };
let experienceLibraryView = "active";

const CATEGORY_LABELS = {
  consistency: "一致性",
  aggregation: "汇总核对",
  cross_domain: "跨专业",
  capacity: "产能",
  version_change: "版本变更",
  terminology: "术语",
  completeness: "完整性",
  evidence: "证据",
  traceability: "可追溯性",
  unknown_scope: "口径不明",
  other: "其他",
};

const SEVERITY_LABELS = { high: "高", medium: "中", low: "低" };

const REVIEW_STATUS_LABELS = {
  pending: "待复核",
  confirmed: "已确认",
  rejected: "已驳回",
  modified: "已修改",
  resolved: "已解决",
};

const REVIEW_STATUS_OPTIONS = [
  ["pending", "待复核"],
  ["confirmed", "确认"],
  ["rejected", "驳回"],
  ["resolved", "已解决"],
];

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category || "其他";
}

function setText(id, value) {
  const node = document.querySelector(`#${id}`);
  if (node) node.textContent = value;
}

function metric(value, suffix = "") {
  return value === null || value === undefined ? "—" : `${value}${suffix}`;
}

function formatBytes(value) {
  const bytes = Number(value);
  if (!Number.isFinite(bytes)) return "—";
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function stageLabel(stage) {
  const labels = Object.fromEntries((globalThis.STAGES || []).map(([id, label]) => [id, label]));
  return labels[stage] || stage || "等待启动";
}

function setWorkflowTab(value) {
  const selected = value === "results" ? "results" : "progress";
  for (const tab of document.querySelectorAll("[data-workflow-tab]")) {
    tab.setAttribute("aria-selected", String(tab.dataset.workflowTab === selected));
  }
  progressRoot.hidden = selected !== "progress";
  resultComponent.hidden = selected !== "results";
}

function renderProgressSummary(snapshot = {}) {
  const status = snapshot.runStatus || "IDLE";
  const statusLabel = ({ IDLE: "未开始", RUNNING: "执行中", READY_FOR_HUMAN_REVIEW: "待专家复核", FAILED: "失败", INTERRUPTED: "已中断" })[status] || status;
  const pill = document.querySelector("#summary-status-pill");
  if (pill) {
    pill.textContent = statusLabel;
    pill.className = `status-pill ${status === "RUNNING" ? "running" : status === "READY_FOR_HUMAN_REVIEW" ? "complete" : ["FAILED", "INTERRUPTED"].includes(status) ? "failed" : "idle"}`;
  }
  setText("summary-run-id", snapshot.runId || "—");
  setText("summary-stage", stageLabel(snapshot.currentStage));
  setText("summary-progress", `${Number(snapshot.progress || 0)}%`);
  setText("summary-elapsed", snapshot.elapsed || "00:00");
}

function renderCompletedSummary(summary, findings, diagnostics) {
  const view = globalThis.diagnosticsAdapter(summary, diagnostics, findings);
  const severity = globalThis.severitySummary(findings);
  currentSummary = summary;
  currentFindings = findings;
  setText("summary-llm", view.llmStatus || "—");
  setText("result-count-badge", view.findingCount);
  setText("result-summary-label", `${view.findingCount} 条最终问题`);
  setText("diag-rule-results", metric(view.ruleResultCount, " 条"));
  setText("diag-rule-ids", metric(view.distinctRuleIdCount, " 个"));
  setText("diag-ai-candidates", metric(view.aiValidCandidateCount, " 条"));
  setText("diag-batches", metric(view.batchCount, " 批"));
  setText("diag-stages", view.stageRecordCount ? `阶段记录 ${view.stageRecordCount} 项` : "—");
  setText("diag-packet-ledger", view.packetLedger.present ? `${metric(view.packetLedger.entryCount, " 条")} · ${formatBytes(view.packetLedger.sizeBytes)}${view.packetLedger.truncated ? " · 已截断" : ""}` : "—");
  setText("diag-candidate-ledger", view.candidateLedger.present ? `${metric(view.candidateLedger.entryCount, " 条")} · ${formatBytes(view.candidateLedger.sizeBytes)}${view.candidateLedger.truncated ? " · 已截断" : ""}` : "—");
  setText("severity-total", severity.total);
  setText("severity-high", severity.high);
  setText("severity-medium", severity.medium);
  setText("severity-low", severity.low);
  setText("origin-rule", view.originCounts.rule);
  setText("origin-hybrid", view.originCounts.hybrid);
  setText("origin-llm", view.originCounts.llm);
  const selector = document.querySelector("#selector-version");
  if (selector) {
    selector.hidden = !view.selectorVersion;
    selector.textContent = view.selectorVersion || "";
  }
  const total = Math.max(1, severity.total);
  const highEnd = severity.high / total * 100;
  const mediumEnd = highEnd + severity.medium / total * 100;
  const donut = document.querySelector("#severity-donut");
  if (donut) donut.style.background = severity.total ? `conic-gradient(var(--workbench-high) 0 ${highEnd}%, var(--workbench-medium) ${highEnd}% ${mediumEnd}%, var(--workbench-low) ${mediumEnd}% 100%)` : "var(--workbench-line)";
  const preview = document.querySelector("#finding-preview");
  if (preview) {
    const labels = { high: "高", medium: "中", low: "低" };
    preview.innerHTML = globalThis.prioritizeFindings(findings, 5).map((finding) => `<button type="button" class="preview-item" data-preview-finding="${escapeHtml(finding.finding_id)}"><span class="preview-severity ${escapeHtml(finding.severity)}">${labels[finding.severity] || "—"}</span><span><strong>${escapeHtml(finding.title)}</strong><small>${escapeHtml(globalThis.findingOriginLabel(finding.origin))}</small></span></button>`).join("") || '<p class="empty-preview">本次没有最终问题。</p>';
  }
}

document.querySelector("#layout-mode")?.addEventListener("change", (event) => layoutController.setMode(event.target.value));
document.querySelector(".workflow-tabs")?.addEventListener("click", (event) => {
  const tab = event.target.closest("[data-workflow-tab]");
  if (tab) setWorkflowTab(tab.dataset.workflowTab);
});
document.querySelector("#view-all-findings")?.addEventListener("click", () => setWorkflowTab("results"));
document.querySelector("#finding-preview")?.addEventListener("click", (event) => {
  const findingId = event.target.closest("[data-preview-finding]")?.dataset.previewFinding;
  if (!findingId) return;
  setWorkflowTab("results");
  document.querySelector(`[data-finding-id="${CSS.escape(findingId)}"]`)?.scrollIntoView({ block: "center" });
});

async function refreshServiceStatus() {
  const node = document.querySelector("#service-status");
  try {
    const response = await fetch("/api/health");
    if (!response.ok) throw new Error("health unavailable");
    node.className = "service-status ok";
    node.innerHTML = "<i></i>本地服务正常";
  } catch (_) {
    node.className = "service-status err";
    node.innerHTML = "<i></i>本地服务不可用";
  }
}

// --- Ruleset status / reload ---------------------------------------------

function renderRulesetStatus(status) {
  if (status && Number.isFinite(Number(status.rule_count))) {
    rulesetDefinitionCount = Number(status.rule_count);
  }
  if (status?.loaded || Number.isFinite(rulesetDefinitionCount)) {
    const total = Number.isFinite(rulesetDefinitionCount) ? rulesetDefinitionCount : "—";
    const enabled = Number.isFinite(currentRunDistinctRuleCount)
      ? `${currentRunDistinctRuleCount}条`
      : "待运行";
    rulesetStatusEl.textContent = `规则库共${total}条，当前启用${enabled}`;
    rulesetStatusEl.className = "ruleset-status ok";
  } else {
    rulesetStatusEl.textContent = "未加载规则库（本次仅做 AI 复核）";
    rulesetStatusEl.className = "ruleset-status warn";
  }
}

async function refreshRulesetStatus() {
  try {
    const response = await fetch("/api/ruleset");
    renderRulesetStatus(response.ok ? await response.json() : null);
  } catch (error) {
    rulesetStatusEl.textContent = "无法连接本地服务";
    rulesetStatusEl.className = "ruleset-status warn";
  }
}

document.querySelector("#reload-ruleset").addEventListener("click", async () => {
  rulesetStatusEl.textContent = "正在加载规则库…";
  rulesetStatusEl.className = "ruleset-status";
  try {
    const response = await fetch("/api/ruleset/reload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    renderRulesetStatus(response.ok ? await response.json() : null);
  } catch (error) {
    rulesetStatusEl.textContent = "无法连接本地服务";
    rulesetStatusEl.className = "ruleset-status warn";
  }
});

// --- Expert experience display-only library -----------------------------

function readExpertExperienceRuns() {
  try {
    const value = JSON.parse(localStorage.getItem(EXPERT_EXPERIENCE_RUNS_KEY) || "{}");
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch (_) {
    return {};
  }
}

function rememberExpertExperienceRun(runId, summary) {
  const runs = readExpertExperienceRuns();
  runs[runId] = {
    totalCount: Number(summary?.total_count || 0),
    updatedAt: summary?.updated_at || null,
  };
  localStorage.setItem(EXPERT_EXPERIENCE_RUNS_KEY, JSON.stringify(runs));
}

function renderExpertExperienceStatus(summary, message = null) {
  if (!expertExperienceStatusEl) return;
  if (summary && Number.isFinite(Number(summary.total_count))) {
    expertExperienceSummary = summary;
    expertExperienceStatusEl.textContent = message || `已加载 ${Number(summary.total_count)} 条专家经验`;
    expertExperienceStatusEl.className = "ruleset-status ok";
    return;
  }
  expertExperienceStatusEl.textContent = message || "无法加载专家经验库";
  expertExperienceStatusEl.className = "ruleset-status warn";
}

function renderExpertExperienceDigest(digest) {
  if (!expertExperienceDigestEl) return;
  if (!digest || !Array.isArray(digest.categories) || !Array.isArray(digest.recent_conclusions)) {
    expertExperienceDigestEl.innerHTML = '<p class="experience-empty">暂时无法归纳专家经验。</p>';
    return;
  }
  if (Number(digest.total_count || 0) === 0) {
    expertExperienceDigestEl.innerHTML = '<p class="experience-empty">尚无已沉淀的专家经验。</p>';
    return;
  }
  const statusLabels = {
    confirmed: "确认", rejected: "驳回", modified: "修正", resolved: "已解决",
  };
  const statusItems = Object.entries(digest.status_counts || {})
    .filter(([, count]) => Number(count) > 0)
    .map(([status, count]) => `<li><span>${escapeHtml(statusLabels[status] || status)}</span><strong>${Number(count)}</strong></li>`)
    .join("");
  const categoryItems = digest.categories
    .map((item) => `<li><span>${escapeHtml(CATEGORY_LABELS[item.category] || item.category)}</span><strong>${Number(item.count)}</strong></li>`)
    .join("");
  const conclusions = digest.recent_conclusions
    .map((item) => `<article class="experience-conclusion">
      <strong>${escapeHtml(item.title)}</strong>
      <small>${escapeHtml(statusLabels[item.review_status] || item.review_status)} · ${escapeHtml(CATEGORY_LABELS[item.category] || item.category)}${item.rule_id ? ` · ${escapeHtml(item.rule_id)}` : ""}</small>
      <p>${escapeHtml(item.expert_note || "专家未填写补充说明")}</p>
    </article>`)
    .join("");
  expertExperienceDigestEl.innerHTML = `
    <div class="experience-digest-grid">
      <section><h3>复核结论</h3><ul>${statusItems}</ul></section>
      <section><h3>经验类别</h3><ul>${categoryItems}</ul></section>
    </div>
    <h3>最近归纳</h3><div class="experience-conclusions">${conclusions}</div>`;
}

function experienceStatusCopy(status) {
  return ({
    NOT_REQUESTED: "尚未请求归纳",
    PENDING: "正在准备专家经验归纳……",
    RUNNING: "正在提炼问题特征、判断依据和处理建议……",
    COMPLETED: "已完成归纳，并沉淀至专家经验库",
    FAILED: "专家复核结果已保存，经验归纳未完成",
    STALE: "专家复核内容已变化，旧归纳已失效",
    DELETED: "该经验已从有效经验库删除",
  })[status] || status || "尚未请求归纳";
}

function renderExperienceJob(job) {
  if (!job) return;
  const card = result.querySelector(`.finding[data-finding-id="${CSS.escape(job.source_finding_id || "")}"]`);
  if (!card) return;
  const flow = card.querySelector('[data-role="experience-flow"]');
  if (!flow) return;
  const status = job.status || "NOT_REQUESTED";
  card.dataset.experienceJobId = job.job_id || "";
  flow.hidden = status === "NOT_REQUESTED";
  flow.className = `experience-flow experience-flow-${status.toLowerCase()}`;
  flow.querySelector('[data-role="experience-flow-copy"]').textContent = experienceStatusCopy(status);
  flow.querySelector('[data-role="experience-retry"]').hidden = status !== "FAILED";
  const summary = flow.querySelector('[data-role="experience-completed-summary"]');
  summary.hidden = status !== "COMPLETED" || !job.experience_summary;
  if (!summary.hidden) summary.textContent = job.experience_summary.experience_title;
}

const experienceController = globalThis.createExpertExperienceController({
  onJobChange: renderExperienceJob,
  onCountChange: (count) => {
    renderExpertExperienceStatus({ total_count: Number(count || 0) });
    const node = document.querySelector('[data-role="experience-active-count"]');
    if (node) node.textContent = Number(count || 0);
  },
});

async function refreshExpertExperienceSummary() {
  try {
    const response = await fetch("/api/expert-experiences/digest?limit=8");
    if (!response.ok) throw new Error("experience summary unavailable");
    const digest = await response.json();
    renderExpertExperienceStatus(digest);
    renderExpertExperienceDigest(digest);
    return digest;
  } catch (_) {
    renderExpertExperienceStatus(null);
    renderExpertExperienceDigest(null);
    return null;
  }
}

function buildExpertExperienceDisplayItems(event, _caseId, runId) {
  const plan = readExpertExperienceRuns()[runId];
  if (!plan || event?.stage !== "RULE_CONFIG" || event?.event_type !== "STAGE_COMPLETED" || event?.status !== "completed") {
    return [];
  }
  const total = Number(plan.totalCount || 0);
  const base = {
    sourceType: "EXPERT_EXPERIENCE_DISPLAY",
    sequence: event.sequence,
    createdAt: event.created_at,
    stage: "RULE_CONFIG",
    title: "专家经验库",
    syntheticDisplayOnly: true,
  };
  return [
    {
      ...base,
      displayId: `expert-experience:${runId}:loading`,
      eventType: "EXPERT_EXPERIENCE_LOADING",
      message: "正在加载专家经验库…",
      status: "running",
    },
    {
      ...base,
      displayId: `expert-experience:${runId}:loaded`,
      eventType: "EXPERT_EXPERIENCE_LOADED",
      message: `专家经验库加载完成，当前共 ${total} 条专家经验`,
      status: "completed",
    },
  ];
}

document.querySelector("#reload-expert-experiences")?.addEventListener("click", () => {
  renderExpertExperienceStatus(null, "正在加载专家经验库…");
  refreshExpertExperienceSummary();
});

if (loadExpertExperiencesEl) {
  loadExpertExperiencesEl.checked = localStorage.getItem(EXPERT_EXPERIENCE_PREFERENCE_KEY) === "true";
  loadExpertExperiencesEl.addEventListener("change", () => {
    localStorage.setItem(EXPERT_EXPERIENCE_PREFERENCE_KEY, String(loadExpertExperiencesEl.checked));
  });
}

// --- LLM config -----------------------------------------------------------

const llmConfigStatus = document.querySelector("#llm-config-status");

async function loadLlmConfig() {
  try {
    const response = await fetch("/api/llm/config");
    if (!response.ok) return;
    const config = await response.json();
    document.querySelector("#llm-provider").value = config.provider || "mock";
    document.querySelector("#llm-base-url").value = config.base_url || "";
    document.querySelector("#llm-model").value = config.model || "";
    document.querySelector("#llm-allow-private").checked = config.allow_private_endpoint === true;
    llmConfigStatus.textContent = config.key_present ? "已配置密钥 ✓" : "未配置密钥";
    llmConfigStatus.className = "llm-config-status" + (config.key_present ? " ok" : "");
  } catch (error) {
    /* leave defaults */
  }
}

document.querySelector("#llm-save").addEventListener("click", async () => {
  const apiKeyEl = document.querySelector("#llm-api-key");
  const payload = {
    provider: document.querySelector("#llm-provider").value,
    base_url: document.querySelector("#llm-base-url").value || null,
    model: document.querySelector("#llm-model").value || null,
    allow_private_endpoint: document.querySelector("#llm-allow-private").checked,
    api_key: apiKeyEl.value || null,
  };
  llmConfigStatus.textContent = "保存中…";
  llmConfigStatus.className = "llm-config-status";
  try {
    const response = await fetch("/api/llm/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      llmConfigStatus.textContent = body.detail || "保存失败";
      llmConfigStatus.className = "llm-config-status err";
      return;
    }
    apiKeyEl.value = "";
    llmConfigStatus.textContent = body.key_present ? "已保存，密钥已配置 ✓" : "已保存";
    llmConfigStatus.className = "llm-config-status ok";
  } catch (error) {
    llmConfigStatus.textContent = "无法连接本地服务";
    llmConfigStatus.className = "llm-config-status err";
  }
});

document.querySelector("#llm-test").addEventListener("click", async () => {
  llmConfigStatus.textContent = "正在测试基础连接…";
  llmConfigStatus.className = "llm-config-status";
  try {
    const response = await fetch("/api/llm/health", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    llmConfigStatus.textContent = body.detail || (body.ok ? "连接正常" : "连接失败");
    llmConfigStatus.className = "llm-config-status " + (body.ok ? "ok" : "err");
  } catch (error) {
    llmConfigStatus.textContent = "无法连接本地服务";
    llmConfigStatus.className = "llm-config-status err";
  }
});

document.querySelector("#llm-structured-test").addEventListener("click", async () => {
  const statusEl = document.querySelector("#llm-structured-status");
  statusEl.textContent = "正在测试结构化输出…";
  statusEl.className = "llm-config-status";
  const metric = (value) => value === null || value === undefined ? "—" : String(value);
  try {
    const response = await fetch("/api/llm/structured-output-test", { method: "POST" });
    const body = await response.json().catch(() => ({}));
    const counts = `候选 ${metric(body.candidate_count)} / 有效 ${metric(body.valid_count)} / 丢弃 ${metric(body.rejected_count)}`;
    statusEl.textContent = `${body.detail || "结构化输出测试失败"}；${counts}`;
    statusEl.className = "llm-config-status " + (body.structured_output_ok ? "ok" : "err");
  } catch (error) {
    statusEl.textContent = "无法连接本地服务";
    statusEl.className = "llm-config-status err";
  }
});

// --- Findings -------------------------------------------------------------

function findingCard(finding) {
  const severity = SEVERITY_LABELS[finding.severity] || finding.severity || "";
  const source = globalThis.findingOriginLabel(finding.origin);
  const humanReview = finding.needs_human_review ? '<span class="chip">需人工复核</span>' : "";
  const parameter = finding.parameter ? `<div class="row"><span class="key">相关参数</span><span>${escapeHtml(finding.parameter)}</span></div>` : "";
  const evidenceCount = Array.isArray(finding.evidence_span_ids) ? finding.evidence_span_ids.length : 0;
  const evidence = evidenceCount
    ? `<div class="row"><span class="key">原文证据</span><span>${evidenceCount} 处（导出报告可查看具体位置）</span></div>`
    : `<div class="row"><span class="key">原文证据</span><span>未检索到对应内容</span></div>`;
  const statusLabel = REVIEW_STATUS_LABELS[finding.review_status] || finding.review_status || "待复核";
  const options = REVIEW_STATUS_OPTIONS
    .map(([value, label]) => `<option value="${value}"${value === finding.review_status ? " selected" : ""}>${label}</option>`)
    .join("");
  return `
    <article class="finding sev-${escapeHtml(finding.severity)}" data-finding-id="${escapeHtml(finding.finding_id)}">
      <header class="finding-head">
        <span class="badge">${escapeHtml(categoryLabel(finding.category))}</span>
        <span class="sev">严重度：${escapeHtml(severity)}</span>
        <span class="src">来源：${escapeHtml(source)}</span>
        ${humanReview}
        <span class="review-state">复核：<b data-role="status-label">${escapeHtml(statusLabel)}</b></span>
      </header>
      <h3 class="finding-title">${escapeHtml(finding.title)}</h3>
      ${parameter}
      <div class="row"><span class="key">说明</span><span>${escapeHtml(finding.description)}</span></div>
      <div class="row"><span class="key">建议</span><span>${escapeHtml(finding.suggestion)}</span></div>
      ${evidence}
      <div class="finding-actions">
        <label>专家结论
          <select data-role="review-status">${options}</select>
        </label>
        <textarea data-role="review-note" placeholder="复核备注（如需修改标题/严重度，请在此写明建议）" maxlength="4000">${escapeHtml(finding.human_note || "")}</textarea>
        <label class="experience-finding-toggle"><input type="checkbox" data-role="expert-experience"${finding.is_expert_experience ? " checked" : ""}>将本次复核结论沉淀至专家经验库</label>
        <button type="button" data-role="save-review">保存复核</button>
        <span data-role="save-hint" class="save-hint"></span>
        <div class="experience-flow experience-flow-${escapeHtml((finding.experience_summary_status || "NOT_REQUESTED").toLowerCase())}" data-role="experience-flow"${finding.experience_summary_status === "NOT_REQUESTED" ? " hidden" : ""}>
          <svg viewBox="0 0 240 56" aria-hidden="true"><path class="experience-flow-line" d="M38 28 H104 M136 28 H202"/><polygon class="experience-shape experience-diamond" points="18,28 38,8 58,28 38,48"/><circle class="experience-shape experience-circle" cx="120" cy="28" r="18"/><polygon class="experience-shape experience-hexagon" points="182,12 202,6 222,18 222,38 202,50 182,44"/><path class="experience-check" d="M194 29 l6 6 12-15"/></svg>
          <span data-role="experience-flow-copy">${escapeHtml(experienceStatusCopy(finding.experience_summary_status))}</span>
          <strong data-role="experience-completed-summary" hidden></strong>
          <button type="button" class="text-action" data-role="experience-retry"${finding.experience_summary_status === "FAILED" ? "" : " hidden"}>重新归纳</button>
        </div>
      </div>
    </article>`;
}

async function saveReview(card) {
  const findingId = card.getAttribute("data-finding-id");
  const statusSelect = card.querySelector('[data-role="review-status"]');
  const noteBox = card.querySelector('[data-role="review-note"]');
  const experienceBox = card.querySelector('[data-role="expert-experience"]');
  const hint = card.querySelector('[data-role="save-hint"]');
  hint.textContent = "保存中…";
  hint.className = "save-hint";
  try {
    const response = await fetch(`/api/cases/${currentCaseId}/runs/${currentRunId}/findings/${findingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        review_status: statusSelect.value,
        human_note: noteBox.value ? noteBox.value : null,
        is_expert_experience: experienceBox?.checked === true,
      }),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      hint.textContent = body.detail || "保存失败";
      hint.className = "save-hint err";
      return;
    }
    const updated = await response.json();
    card.querySelector('[data-role="status-label"]').textContent =
      REVIEW_STATUS_LABELS[updated.review_status] || updated.review_status;
    if (experienceBox) experienceBox.checked = updated.is_expert_experience === true;
    renderExpertExperienceStatus({ total_count: updated.expert_experience_total_count });
    hint.textContent = updated.expert_experience_requested
      ? `专家复核结果已保存；${experienceStatusCopy(updated.experience_summary_status)}`
      : "专家复核结果已保存";
    hint.className = "save-hint ok";
    if (updated.experience_summary_job_id) experienceController.track({
      job_id: updated.experience_summary_job_id,
      source_finding_id: updated.source_finding_id,
      status: updated.experience_summary_status,
      expert_experience_total_count: updated.expert_experience_total_count,
    });
  } catch (error) {
    hint.textContent = "无法连接本地服务";
    hint.className = "save-hint err";
  }
}

result.addEventListener("click", (event) => {
  const retry = event.target.closest('[data-role="experience-retry"]');
  if (retry) {
    const card = retry.closest(".finding");
    retry.disabled = true;
    fetch(`/api/cases/${currentCaseId}/runs/${currentRunId}/findings/${encodeURIComponent(card.dataset.findingId)}/expert-experience/retry`, { method: "POST" })
      .then(async (response) => {
        if (!response.ok) throw new Error("retry failed");
        const job = await response.json();
        experienceController.track(job);
      })
      .catch(() => { retry.hidden = false; })
      .finally(() => { retry.disabled = false; });
    return;
  }
  const button = event.target.closest('[data-role="save-review"]');
  if (!button) return;
  const card = button.closest(".finding");
  if (card) saveReview(card);
});

function renderFailedReview(failure) {
  const detail = failure?.failure_detail || "本次审查未完成，请重试或联系管理员。";
  result.innerHTML = `
    <div class="failure" role="alert">
      <h2>本次审查未完成</h2>
      <p>${escapeHtml(detail)}</p>
    </div>`;
  setText("result-summary-label", "运行失败");
}

function renderResult(summary, findings, diagnostics = {}) {
  currentCaseId = summary.case_id;
  currentRunId = summary.run_id;
  const uiState = globalThis.deriveReviewUiState(summary);
  if (uiState.failed || !uiState.completed) {
    renderFailedReview(summary);
    return;
  }
  const parts = [];
  parts.push(`<div class="summary-line">${escapeHtml(uiState.llmStatusLabel)}</div>`);
  if (uiState.showPartialAiNotice) {
    parts.push('<div class="warn">AI已复核部分重点证据，其余内容已完成确定性规则检查</div>');
  }
  if (uiState.showNoValidPreliminary) {
    parts.push('<div class="warn">本次没有形成有效初审结果。规则库未加载，且AI复核未完成，请修复配置后重新审查。</div>');
  } else if (uiState.showRuleOnlyNotice) {
    parts.push('<div class="warn">当前问题清单仅包含确定性规则结果。</div>');
  } else if (uiState.showAiOnlyNotice) {
    parts.push('<div class="warn">未加载规则库，本次结果仅来自 AI 复核。</div>');
  }
  if (summary.rules_loaded) {
    parts.push(`<div class="summary-line">已应用 ${summary.rule_count} 条审查规则。</div>`);
  }
  parts.push(`<div class="summary-line">案例编号：${escapeHtml(summary.case_id)}　发现问题：${summary.finding_count} 条　提取参数：${summary.fact_count} 个</div>`);
  if (uiState.showExports) {
    parts.push(`
      <div class="export-bar">
        <a class="export-btn" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/xlsx">导出 Excel（含问题位置）</a>
        <a class="export-btn" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/docx">导出 Word 报告</a>
        <a class="export-btn ghost" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/anonymous">导出匿名包</a>
      </div>`);
  }
  if (uiState.showNoValidPreliminary) {
    // There is no trustworthy result set to render or review.
  } else if (uiState.showEmpty) {
    parts.push('<div class="empty">本次初审未发现问题。这并不代表方案完全正确，请仍由专家复核。</div>');
  } else if (uiState.showExpertReview) {
    parts.push(findings.map(findingCard).join(""));
  }
  if (uiState.showExpertReview) {
    parts.push('<p class="foot">以上为初审结果，不是正式审查结论。请由具备资质的专家复核后再作决定。</p>');
  }
  result.innerHTML = parts.join("");
  for (const finding of findings) {
    if (finding.experience_id && ["PENDING", "RUNNING"].includes(finding.experience_summary_status)) {
      experienceController.track({
        job_id: finding.experience_id,
        source_finding_id: finding.finding_id,
        status: finding.experience_summary_status,
      });
    }
  }
  renderCompletedSummary(summary, findings, diagnostics);
}

async function loadCompletedRun(progressState, caseId, runId) {
  currentCaseId = caseId;
  currentRunId = runId;
  result.hidden = false;
  if (["FAILED", "INTERRUPTED"].includes(progressState.runStatus)) {
    renderFailedReview({
      failure_detail: progressState.runStatus === "INTERRUPTED"
        ? "上次审查因应用中断未完成，请重新运行。"
        : "审查任务执行失败，请检查配置或重新运行。",
    });
    return;
  }
  try {
    const [summaryResponse, findingsResponse, diagnosticsResponse] = await Promise.all([
      fetch(`/api/cases/${encodeURIComponent(caseId)}/runs/${encodeURIComponent(runId)}`),
      fetch(`/api/cases/${encodeURIComponent(caseId)}/runs/${encodeURIComponent(runId)}/findings`),
      fetch(`/api/cases/${encodeURIComponent(caseId)}/runs/${encodeURIComponent(runId)}/diagnostics`).catch(() => null),
    ]);
    if (!summaryResponse.ok || !findingsResponse.ok) throw new Error("result unavailable");
    const summary = await summaryResponse.json();
    const findings = await findingsResponse.json();
    const diagnostics = diagnosticsResponse?.ok ? await diagnosticsResponse.json() : {};
    const diagnosticsView = globalThis.diagnosticsAdapter(summary, diagnostics, findings);
    currentRunDistinctRuleCount = diagnosticsView.distinctRuleIdCount;
    renderRulesetStatus({ loaded: summary.rules_loaded !== false });
    summary.rules_loaded = Number(progressState.metrics.applicable_rule_count || 0) > 0;
    summary.rule_count = Number(progressState.metrics.applicable_rule_count || 0);
    renderResult(summary, findings, diagnostics);
  } catch (_) {
    renderFailedReview({ failure_detail: "初审已结束，但结果加载失败，请刷新页面重试。" });
  }
}

const progressController = globalThis.createReviewProgressController(
  document.querySelector("#review-progress-root"),
  {
    onTerminal: loadCompletedRun,
    onPlaybackStart: () => { result.hidden = true; },
    onStateChange: renderProgressSummary,
    buildDisplayOnlyItems: buildExpertExperienceDisplayItems,
  },
);

document.querySelector("#upload").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = document.querySelector("#file").files[0];
  if (!file) {
    result.textContent = "请先选择一个 DOCX 文件。";
    return;
  }
  const body = new FormData();
  body.append("file", file);
  result.textContent = "正在上传并建立本地案例…";
  try {
    const upload = await fetch("/api/cases", { method: "POST", body });
    const uploaded = await upload.json();
    if (!upload.ok) {
      result.textContent = uploaded.detail || "上传失败，请检查文件是否为 DOCX。";
      return;
    }
    setText("case-id-display", uploaded.case_id);
    result.textContent = "正在创建智能初审任务…";
    const review = await fetch(`/api/cases/${uploaded.case_id}/review-jobs`, { method: "POST" });
    const accepted = await review.json();
    if (!review.ok) {
      result.textContent = accepted.detail || "初审任务创建失败。";
      return;
    }
    result.textContent = "";
    currentRunDistinctRuleCount = null;
    renderRulesetStatus({ loaded: true });
    if (loadExpertExperiencesEl?.checked) {
      const summary = await refreshExpertExperienceSummary();
      if (summary) rememberExpertExperienceRun(accepted.run_id, summary);
    }
    progressController.start(uploaded.case_id, accepted.run_id);
  } catch (error) {
    result.textContent = "无法连接本地服务，请确认服务已启动。";
  }
});

function showExperienceLibrary(show = true) {
  experienceLibraryEl.hidden = !show;
  document.querySelector("#review-component").hidden = show;
  resultComponent.hidden = show ? true : !currentSummary;
  if (show) loadExperienceLibrary();
}

function experienceLibraryCard(item) {
  const summary = item.summary;
  const actions = item.status === "DELETED"
    ? `<button type="button" data-experience-action="restore">恢复</button>`
    : item.status === "FAILED"
      ? `<button type="button" data-experience-action="retry">重新归纳</button>`
      : `<button type="button" data-experience-action="delete">删除</button>`;
  return `<article class="experience-library-item" data-experience-id="${escapeHtml(item.experience_id)}" data-case-id="${escapeHtml(item.source_case_id)}" data-run-id="${escapeHtml(item.source_run_id)}" data-finding-id="${escapeHtml(item.source_finding_id)}">
    <header><span class="badge">${escapeHtml(categoryLabel(item.category))}</span><span class="sev">${escapeHtml(SEVERITY_LABELS[item.severity] || item.severity)}</span><span class="status-pill">${escapeHtml(item.status)}</span></header>
    <h3>${escapeHtml(summary?.experience_title || item.title)}</h3>
    <div class="experience-judgment"><strong>专家原结论</strong><p>${escapeHtml(REVIEW_STATUS_LABELS[item.expert_review_status] || item.expert_review_status)}${item.expert_note ? `：${escapeHtml(item.expert_note)}` : ""}</p></div>
    ${summary ? `<details><summary>查看模型归纳</summary><p><b>问题模式：</b>${escapeHtml(summary.problem_pattern)}</p><p><b>判断依据：</b>${summary.judgment_basis.map(escapeHtml).join("；")}</p><p><b>处理建议：</b>${summary.recommended_action.map(escapeHtml).join("；")}</p><p><b>适用范围：</b>${escapeHtml(summary.applicable_scope)}</p><p><b>关键词：</b>${summary.keywords.map(escapeHtml).join("、")}</p></details>` : `<p class="experience-empty">${escapeHtml(item.status === "FAILED" ? "归纳未完成，可重新归纳。" : "暂无归纳内容。")}</p>`}
    <footer><button type="button" data-experience-action="source">查看来源</button>${actions}</footer>
  </article>`;
}

async function loadExperienceLibrary() {
  const form = document.querySelector("#experience-library-filters");
  const params = new URLSearchParams({ view: experienceLibraryView, page: "1", page_size: "50" });
  for (const [key, value] of new FormData(form).entries()) if (value) params.set(key, value);
  experienceLibraryListEl.innerHTML = '<p class="experience-empty">正在读取专家经验…</p>';
  try {
    const response = await fetch(`/api/expert-experiences?${params}`);
    if (!response.ok) throw new Error("library unavailable");
    const payload = await response.json();
    document.querySelector('[data-role="experience-active-count"]').textContent = payload.active_count;
    document.querySelector('[data-role="experience-deleted-count"]').textContent = payload.deleted_count;
    document.querySelector('[data-role="experience-failed-count"]').textContent = payload.failed_count;
    experienceLibraryListEl.innerHTML = payload.items.length
      ? payload.items.map(experienceLibraryCard).join("")
      : '<p class="experience-empty">当前筛选条件下没有专家经验。</p>';
  } catch (_) {
    experienceLibraryListEl.innerHTML = '<p class="experience-empty">无法读取专家经验库。</p>';
  }
}

document.querySelector('[data-role="experience-library-nav"]')?.addEventListener("click", (event) => {
  event.preventDefault(); showExperienceLibrary(true);
});
document.querySelector('[data-role="experience-library-close"]')?.addEventListener("click", () => showExperienceLibrary(false));
document.querySelector("#experience-library-filters")?.addEventListener("submit", (event) => { event.preventDefault(); loadExperienceLibrary(); });
document.querySelectorAll("[data-experience-view]").forEach((button) => button.addEventListener("click", () => {
  experienceLibraryView = button.dataset.experienceView;
  document.querySelectorAll("[data-experience-view]").forEach((candidate) => candidate.setAttribute("aria-selected", String(candidate === button)));
  loadExperienceLibrary();
}));

experienceLibraryListEl?.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-experience-action]");
  const card = button?.closest("[data-experience-id]");
  if (!button || !card) return;
  const action = button.dataset.experienceAction;
  if (action === "source") {
    showExperienceLibrary(false);
    await loadCompletedRun({ runStatus: "READY_FOR_HUMAN_REVIEW" }, card.dataset.caseId, card.dataset.runId);
    result.querySelector(`.finding[data-finding-id="${CSS.escape(card.dataset.findingId)}"]`)?.scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }
  if (action === "delete" && !confirm("确认从专家经验库删除？\n\n删除后不再计数，但不会删除原审查问题、专家复核、历史Run或导出结果。")) return;
  button.disabled = true;
  try {
    let response;
    if (action === "delete") response = await fetch(`/api/expert-experiences/${card.dataset.experienceId}`, { method: "DELETE", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (action === "restore") response = await fetch(`/api/expert-experiences/${card.dataset.experienceId}/restore`, { method: "POST" });
    if (action === "retry") response = await fetch(`/api/cases/${card.dataset.caseId}/runs/${card.dataset.runId}/findings/${card.dataset.findingId}/expert-experience/retry`, { method: "POST" });
    if (!response?.ok) throw new Error("mutation failed");
    const payload = await response.json();
    if (payload.job_id) experienceController.track(payload);
    await Promise.all([loadExperienceLibrary(), refreshExpertExperienceSummary()]);
  } catch (_) {
    button.disabled = false;
  }
});

refreshRulesetStatus();
refreshExpertExperienceSummary();
loadLlmConfig();
progressController.restore();
refreshServiceStatus();

document.querySelector("#file")?.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  setText("file-name-display", file?.name || "尚未选择");
  setText("file-size-display", file ? formatBytes(file.size) : "—");
});
