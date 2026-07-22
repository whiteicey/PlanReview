"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { deriveReviewUiState, llmStatusLabel, findingOriginLabel } = require("./review_state.js");

test("failed review hides empty, export, and expert-review UI", () => {
  assert.deepEqual(
    deriveReviewUiState({ final_status: "FAILED", finding_count: 0 }),
    {
      completed: false,
      failed: true,
      showEmpty: false,
      showExports: false,
      showExpertReview: false,
      llmStatus: "NOT_RUN",
      llmStatusLabel: "AI未运行",
      showPartialAiNotice: false,
      showRuleOnlyNotice: false,
      showAiOnlyNotice: false,
      showNoValidPreliminary: false,
    },
  );
});

test("completed rule review can show an empty result and controls", () => {
  assert.deepEqual(
    deriveReviewUiState({
      final_status: "READY_FOR_HUMAN_REVIEW",
      rules_loaded: true,
      llm_status: "NOT_RUN",
      finding_count: 0,
    }),
    {
      completed: true,
      failed: false,
      showEmpty: true,
      showExports: true,
      showExpertReview: true,
      llmStatus: "NOT_RUN",
      llmStatusLabel: "AI未运行",
      showPartialAiNotice: false,
      showRuleOnlyNotice: true,
      showAiOnlyNotice: false,
      showNoValidPreliminary: false,
    },
  );
});

test("LLM status and finding origins are explicit", () => {
  assert.equal(llmStatusLabel("COMPLETED_PARTIAL"), "AI部分重点证据复核已完成");
  assert.equal(llmStatusLabel("PROVIDER_ERROR"), "AI 服务调用失败，本次仅保留确定性规则结果");
  assert.equal(llmStatusLabel("VALIDATION_FAILED"), "AI 已返回内容，但未通过结构化格式校验");
  assert.equal(findingOriginLabel("rule"), "规则");
  assert.equal(findingOriginLabel("hybrid"), "规则+AI");
  assert.equal(findingOriginLabel("human"), "专家补充");
});

test("validation reason selects only a redacted frontend error category", () => {
  const evidence = deriveReviewUiState({
    final_status: "READY_FOR_HUMAN_REVIEW", rules_loaded: true,
    llm_status: "VALIDATION_FAILED", validation_reason_code: "invalid_evidence",
  });
  assert.equal(evidence.llmStatusLabel, "AI 结果引用证据不符合要求，相关结果已丢弃");
  const format = deriveReviewUiState({
    final_status: "READY_FOR_HUMAN_REVIEW", rules_loaded: true,
    llm_status: "VALIDATION_FAILED", validation_reason_code: "truncated_json",
  });
  assert.equal(format.llmStatusLabel, "AI 已返回内容，但未通过结构化格式校验");
});

test("partial AI completion exposes the fixed limited-coverage notice flag", () => {
  const state = deriveReviewUiState({
    final_status: "READY_FOR_HUMAN_REVIEW",
    rules_loaded: true,
    llm_status: "COMPLETED_PARTIAL",
    finding_count: 0,
  });
  assert.equal(state.showPartialAiNotice, true);
});

test("mock never claims that report evidence received real AI review", () => {
  const state = deriveReviewUiState({
    final_status: "READY_FOR_HUMAN_REVIEW",
    rules_loaded: true,
    llm_provider: "mock",
    llm_status: "COMPLETED_PARTIAL",
    finding_count: 0,
  });
  assert.equal(state.showPartialAiNotice, false);
  assert.equal(state.llmStatusLabel, "模拟 AI 调用链已完成（未执行真实 AI 业务分析）");
});

test("missing rules and incomplete AI do not claim a valid preliminary result", () => {
  const state = deriveReviewUiState({
    final_status: "READY_FOR_HUMAN_REVIEW",
    rules_loaded: false,
    llm_status: "PROVIDER_ERROR",
    finding_count: 0,
  });
  assert.equal(state.completed, true);
  assert.equal(state.showEmpty, false);
  assert.equal(state.showExports, false);
  assert.equal(state.showExpertReview, false);
  assert.equal(state.showRuleOnlyNotice, false);
  assert.equal(state.showAiOnlyNotice, false);
  assert.equal(state.showNoValidPreliminary, true);
});

for (const llmStatus of [
  "NOT_RUN",
  "CONFIGURATION_ERROR",
  "PROVIDER_ERROR",
  "INPUT_LIMIT_EXCEEDED",
  "VALIDATION_FAILED",
]) {
  test(`missing rules and ${llmStatus} has no valid preliminary result`, () => {
    const state = deriveReviewUiState({
      final_status: "READY_FOR_HUMAN_REVIEW",
      rules_loaded: false,
      llm_status: llmStatus,
      finding_count: 0,
    });
    assert.equal(state.showNoValidPreliminary, true);
    assert.equal(state.showEmpty, false);
    assert.equal(state.showExports, false);
    assert.equal(state.showExpertReview, false);
  });
}

for (const llmStatus of ["COMPLETED", "COMPLETED_PARTIAL"]) {
  test(`completed AI status ${llmStatus} remains a valid AI-only result`, () => {
    const state = deriveReviewUiState({
      final_status: "READY_FOR_HUMAN_REVIEW",
      rules_loaded: false,
      llm_status: llmStatus,
      finding_count: 0,
    });
    assert.equal(state.showNoValidPreliminary, false);
    assert.equal(state.showAiOnlyNotice, true);
    assert.equal(state.showEmpty, true);
    assert.equal(state.showExports, true);
    assert.equal(state.showExpertReview, true);
  });
}
