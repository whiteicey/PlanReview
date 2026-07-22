"use strict";

(function (root) {
  const SEVERITIES = Object.freeze(["high", "medium", "low"]);
  const SEVERITY_ORDER = Object.freeze({ high: 0, medium: 1, low: 2 });

  function finiteNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function ledgerView(ledger) {
    const value = ledger && typeof ledger === "object" ? ledger : {};
    return Object.freeze({
      present: Boolean(ledger && typeof ledger === "object"),
      entryCount: finiteNumber(value.ledger_entry_count),
      sizeBytes: finiteNumber(value.ledger_size_bytes),
      truncated: typeof value.ledger_truncated === "boolean" ? value.ledger_truncated : null,
    });
  }

  function diagnosticsAdapter(summary = {}, diagnostics = {}, findings = []) {
    const integrity = diagnostics?.integrity && typeof diagnostics.integrity === "object" ? diagnostics.integrity : {};
    const stages = Array.isArray(summary?.stages) ? summary.stages.filter((item) => typeof item === "string") : [];
    const originCounts = { rule: 0, hybrid: 0, llm: 0, other: 0 };
    for (const finding of Array.isArray(findings) ? findings : []) {
      const origin = Object.hasOwn(originCounts, finding?.origin) ? finding.origin : "other";
      originCounts[origin] += 1;
    }
    return Object.freeze({
      finalStatus: typeof summary?.final_status === "string" ? summary.final_status : null,
      llmStatus: typeof summary?.llm_status === "string" ? summary.llm_status : null,
      findingCount: finiteNumber(summary?.finding_count) ?? findings.length,
      aiValidCandidateCount: finiteNumber(summary?.llm_finding_count),
      stageRecordCount: stages.length,
      allStagesCompleted: null,
      selectorVersion: typeof diagnostics?.evidence_selector_version === "string" ? diagnostics.evidence_selector_version : null,
      ruleResultCount: finiteNumber(integrity.rule_result_count),
      distinctRuleIdCount: finiteNumber(integrity.distinct_rule_id_count),
      batchCount: finiteNumber(integrity.batch_count),
      packetLedger: ledgerView(diagnostics?.packet_lifecycle_ledger),
      candidateLedger: ledgerView(diagnostics?.ai_candidate_lifecycle_ledger),
      originCounts: Object.freeze(originCounts),
    });
  }

  function severitySummary(findings = []) {
    const counts = { high: 0, medium: 0, low: 0 };
    for (const finding of findings) {
      if (SEVERITIES.includes(finding?.severity)) counts[finding.severity] += 1;
    }
    return Object.freeze({ ...counts, total: counts.high + counts.medium + counts.low });
  }

  function prioritizeFindings(findings = [], limit = 5) {
    return [...findings]
      .sort((left, right) => (SEVERITY_ORDER[left?.severity] ?? 99) - (SEVERITY_ORDER[right?.severity] ?? 99))
      .slice(0, Math.max(0, Number(limit) || 0));
  }

  function safeProgressSnapshot(state = {}) {
    return Object.freeze({
      caseId: state.caseId || null,
      runId: state.runId || null,
      runStatus: state.runStatus || "IDLE",
      currentStage: state.currentStage || null,
      currentMessage: state.currentMessage || "尚未开始审查",
      progress: finiteNumber(state.progress) ?? 0,
      elapsed: state.elapsed || "00:00",
      displayComplete: Boolean(state.displayComplete),
      metrics: Object.freeze({ ...(state.metrics || {}) }),
    });
  }

  const api = { finiteNumber, ledgerView, diagnosticsAdapter, severitySummary, prioritizeFindings, safeProgressSnapshot };
  Object.assign(root, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
