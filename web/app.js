const result = document.querySelector("#result");
const rulesetStatusEl = document.querySelector("#ruleset-status");
let currentCaseId = null;

const CATEGORY_LABELS = {
  consistency: "一致性",
  aggregation: "汇总核对",
  cross_domain: "跨专业",
  capacity: "产能",
  version_change: "版本变更",
  terminology: "术语",
  completeness: "完整性",
  evidence: "证据",
  unknown_scope: "口径不明",
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

// --- Ruleset status / reload ---------------------------------------------

function renderRulesetStatus(status) {
  if (status && status.loaded) {
    rulesetStatusEl.textContent = `已加载 ${status.rule_count} 条审查规则`;
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
  llmConfigStatus.textContent = "正在测试连接…";
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

// --- Findings -------------------------------------------------------------

function findingCard(finding) {
  const severity = SEVERITY_LABELS[finding.severity] || finding.severity || "";
  const source = finding.origin === "llm" ? "AI 复核" : "规则";
  const humanReview = finding.needs_human_review ? '<span class="chip">需人工复核</span>' : "";
  const parameter = finding.parameter ? `<div class="row"><span class="key">相关参数</span><span>${escapeHtml(finding.parameter)}</span></div>` : "";
  const evidence = `<div class="row"><span class="key">原文证据</span><span>${finding.evidence_span_ids.length} 处（导出报告可查看具体位置）</span></div>`;
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
        <button type="button" data-role="save-review">保存复核</button>
        <span data-role="save-hint" class="save-hint"></span>
      </div>
    </article>`;
}

async function saveReview(card) {
  const findingId = card.getAttribute("data-finding-id");
  const statusSelect = card.querySelector('[data-role="review-status"]');
  const noteBox = card.querySelector('[data-role="review-note"]');
  const hint = card.querySelector('[data-role="save-hint"]');
  hint.textContent = "保存中…";
  hint.className = "save-hint";
  try {
    const response = await fetch(`/api/findings/${findingId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        case_id: currentCaseId,
        review_status: statusSelect.value,
        human_note: noteBox.value ? noteBox.value : null,
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
    hint.textContent = "已保存";
    hint.className = "save-hint ok";
  } catch (error) {
    hint.textContent = "无法连接本地服务";
    hint.className = "save-hint err";
  }
}

result.addEventListener("click", (event) => {
  const button = event.target.closest('[data-role="save-review"]');
  if (!button) return;
  const card = button.closest(".finding");
  if (card) saveReview(card);
});

function renderResult(summary, findings) {
  currentCaseId = summary.case_id;
  const parts = [];
  if (!summary.rules_loaded) {
    parts.push('<div class="warn">未加载规则库，本次仅做 AI 复核。点击上方「加载 / 重新加载规则库」后重试，或联系管理员配置示例规则包。</div>');
  } else {
    parts.push(`<div class="summary-line">已应用 ${summary.rule_count} 条审查规则。</div>`);
  }
  parts.push(`<div class="summary-line">案例编号：${escapeHtml(summary.case_id)}　发现问题：${summary.finding_count} 条　提取参数：${summary.fact_count} 个</div>`);
  parts.push(`
    <div class="export-bar">
      <a class="export-btn" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/xlsx">导出 Excel（含问题位置）</a>
      <a class="export-btn" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/docx">导出 Word 报告</a>
      <a class="export-btn ghost" href="/api/cases/${encodeURIComponent(summary.case_id)}/exports/anonymous">导出匿名包</a>
    </div>`);
  if (!findings.length) {
    parts.push('<div class="empty">本次未发现规则可判定的问题。这并不代表方案完全正确，请仍由专家复核。</div>');
  } else {
    parts.push(findings.map(findingCard).join(""));
  }
  parts.push('<p class="foot">以上为 AI 初审结果，不是正式审查结论。请由具备资质的专家复核后再作决定。</p>');
  result.innerHTML = parts.join("");
}

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
    result.textContent = "正在执行初审，请稍候…";
    const review = await fetch(`/api/cases/${uploaded.case_id}/review`, { method: "POST" });
    const summary = await review.json();
    if (!review.ok) {
      result.textContent = summary.detail || "初审失败。";
      return;
    }
    const findingsResponse = await fetch(`/api/cases/${uploaded.case_id}/findings`);
    const findings = findingsResponse.ok ? await findingsResponse.json() : [];
    renderResult(summary, findings);
  } catch (error) {
    result.textContent = "无法连接本地服务，请确认服务已启动。";
  }
});

refreshRulesetStatus();
loadLlmConfig();
