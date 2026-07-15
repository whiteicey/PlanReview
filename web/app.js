const result = document.querySelector("#result");

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

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[ch]));
}

function categoryLabel(category) {
  return CATEGORY_LABELS[category] || category || "其他";
}

function findingCard(finding) {
  const severity = SEVERITY_LABELS[finding.severity] || finding.severity || "";
  const source = finding.origin === "llm" ? "AI 复核" : "规则";
  const humanReview = finding.needs_human_review ? '<span class="chip">需人工复核</span>' : "";
  const parameter = finding.parameter ? `<div class="row"><span class="key">相关参数</span><span>${escapeHtml(finding.parameter)}</span></div>` : "";
  const evidence = `<div class="row"><span class="key">原文证据</span><span>${finding.evidence_span_ids.length} 处（导出报告可查看具体位置）</span></div>`;
  return `
    <article class="finding sev-${escapeHtml(finding.severity)}">
      <header class="finding-head">
        <span class="badge">${escapeHtml(categoryLabel(finding.category))}</span>
        <span class="sev">严重度：${escapeHtml(severity)}</span>
        <span class="src">来源：${escapeHtml(source)}</span>
        ${humanReview}
      </header>
      <h3 class="finding-title">${escapeHtml(finding.title)}</h3>
      ${parameter}
      <div class="row"><span class="key">说明</span><span>${escapeHtml(finding.description)}</span></div>
      <div class="row"><span class="key">建议</span><span>${escapeHtml(finding.suggestion)}</span></div>
      ${evidence}
    </article>`;
}

function renderResult(summary, findings) {
  const parts = [];
  if (!summary.rules_loaded) {
    parts.push('<div class="warn">未加载规则库，本次仅做 AI 复核。如需完整规则检查，请联系管理员配置示例规则包（REVIEW_DEMO_ROOT）。</div>');
  } else {
    parts.push(`<div class="summary-line">已应用 ${summary.rule_count} 条审查规则。</div>`);
  }
  parts.push(`<div class="summary-line">案例编号：${escapeHtml(summary.case_id)}　发现问题：${summary.finding_count} 条　提取参数：${summary.fact_count} 个</div>`);
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
