"use strict";

const LLM_STATUS_LABELS = Object.freeze({
  NOT_RUN: "AI未运行",
  COMPLETED: "AI复核已完成",
  COMPLETED_PARTIAL: "AI部分重点证据复核已完成",
  CONFIGURATION_ERROR: "AI配置不完整",
  PROVIDER_ERROR: "AI 服务调用失败，本次仅保留确定性规则结果",
  INPUT_LIMIT_EXCEEDED: "没有可送审证据或输入超限",
  VALIDATION_FAILED: "AI 已返回内容，但未通过结构化格式校验",
});

const FINDING_ORIGIN_LABELS = Object.freeze({
  rule: "规则",
  llm: "AI复核",
  hybrid: "规则+AI",
  human: "专家补充",
});

function llmStatusLabel(status) {
  return LLM_STATUS_LABELS[status] || "AI状态未知";
}

function findingOriginLabel(origin) {
  return FINDING_ORIGIN_LABELS[origin] || "来源未知";
}

function summaryLlmStatusLabel(summary) {
  if (summary?.llm_status === "VALIDATION_FAILED" && summary?.validation_reason_code === "invalid_evidence") {
    return "AI 结果引用证据不符合要求，相关结果已丢弃";
  }
  return llmStatusLabel(summary?.llm_status || "NOT_RUN");
}

function deriveReviewUiState(summary) {
  const completed = summary?.final_status === "READY_FOR_HUMAN_REVIEW";
  const failed = summary?.final_status === "FAILED";
  const llmComplete = ["COMPLETED", "COMPLETED_PARTIAL"].includes(summary?.llm_status)
    && summary?.llm_provider !== "mock";
  const rulesAvailable = summary?.rules_loaded === true;
  const hasValidPreliminary = completed && (rulesAvailable || llmComplete);
  return {
    completed,
    failed,
    showEmpty: hasValidPreliminary && Number(summary?.finding_count) === 0,
    showExports: hasValidPreliminary,
    showExpertReview: hasValidPreliminary,
    llmStatus: summary?.llm_status || "NOT_RUN",
    llmStatusLabel: summary?.llm_provider === "mock" && ["COMPLETED", "COMPLETED_PARTIAL"].includes(summary?.llm_status)
      ? "模拟 AI 调用链已完成（未执行真实 AI 业务分析）"
      : summaryLlmStatusLabel(summary),
    showPartialAiNotice: completed && summary?.llm_provider !== "mock" && summary?.llm_status === "COMPLETED_PARTIAL",
    showRuleOnlyNotice: completed && rulesAvailable && !llmComplete,
    showAiOnlyNotice: completed && !rulesAvailable && llmComplete,
    showNoValidPreliminary: completed && !rulesAvailable && !llmComplete,
  };
}

if (typeof globalThis !== "undefined") {
  globalThis.deriveReviewUiState = deriveReviewUiState;
  globalThis.llmStatusLabel = llmStatusLabel;
  globalThis.findingOriginLabel = findingOriginLabel;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { deriveReviewUiState, llmStatusLabel, findingOriginLabel };
}
