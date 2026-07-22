"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { LOG_LIMIT, escapeHtml, createProgressState, applyProgressBatch, selectLogEvents, selectDisplayItems } = require("./review_progress.js");

function event(sequence, stage, event_type, status, details = {}, message = "事件") {
  return { sequence, stage, event_type, status, details, message, created_at: "2026-07-20T10:00:00Z" };
}

test("events are sorted, deduplicated and restored from sequence zero", () => {
  const state = createProgressState();
  applyProgressBatch(state, { run_status: "RUNNING", last_sequence: 3, events: [event(3, "DOCUMENT_PARSE", "STAGE_COMPLETED", "completed"), event(1, "INPUT_VALIDATION", "STAGE_COMPLETED", "completed"), event(1, "INPUT_VALIDATION", "STAGE_COMPLETED", "completed")] });
  assert.deepEqual(state.events.map((item) => item.sequence), [1, 3]);
  assert.equal(state.progress, 20);
});

test("successful terminal run reaches 100 while failed and interrupted never do", () => {
  const completed = createProgressState();
  applyProgressBatch(completed, { run_status: "READY_FOR_HUMAN_REVIEW", last_sequence: 1, events: [] });
  assert.equal(completed.progress, 100);
  for (const status of ["FAILED", "INTERRUPTED"]) {
    const failed = createProgressState();
    applyProgressBatch(failed, { run_status: status, last_sequence: 1, events: [event(1, "INPUT_VALIDATION", "STAGE_COMPLETED", "completed")] });
    assert.ok(failed.progress < 100);
  }
});

test("rule progress uses real completed count and skipped stages count", () => {
  const state = createProgressState();
  applyProgressBatch(state, { run_status: "RUNNING", last_sequence: 2, events: [
    event(1, "RULE_CHECK", "STAGE_STARTED", "running", { applicable_rule_count: 4, completed_rule_count: 0 }),
    event(2, "RULE_CHECK", "RULE_COMPLETED", "completed", { rule_id: "R1", result: "PASS", completed_rule_count: 2 }),
    event(3, "AI_REVIEW", "STAGE_COMPLETED", "skipped"),
  ] });
  assert.equal(state.progress, 21);
});

test("PASS is folded while FAIL and UNKNOWN are prioritized under render cap", () => {
  const events = [];
  for (let index = 1; index <= LOG_LIMIT + 20; index += 1) events.push(event(index, "RULE_CHECK", "RULE_COMPLETED", "completed", { rule_id: `R${index}`, result: "PASS" }));
  events.push(event(999, "RULE_CHECK", "RULE_COMPLETED", "partial", { rule_id: "BAD", result: "FAIL" }));
  const selected = selectLogEvents(events, false);
  assert.equal(selected.events.length, 1);
  assert.equal(selected.events[0].details.result, "FAIL");
  const expanded = selectLogEvents(events, true);
  assert.equal(expanded.events.length, LOG_LIMIT);
  assert.ok(expanded.events.some((item) => item.details.result === "FAIL"));
});

test("dynamic text is HTML escaped", () => {
  assert.equal(escapeHtml('<img src=x onerror="bad">'), "&lt;img src=x onerror=&quot;bad&quot;&gt;");
});

test("AI coverage metrics survive ordered replay without document content", () => {
  const state = createProgressState();
  applyProgressBatch(state, { run_status: "RUNNING", last_sequence: 1, events: [
    event(1, "AI_EVIDENCE", "STAGE_COMPLETED", "completed", {
      available_span_count: 135,
      original_evidence_count: 135,
      selected_span_count: 40,
      selected_character_count: 23888,
      ai_coverage_ratio: 29.6,
      coverage_ratio: 0.2963,
    }, "已选择重点证据"),
  ] });
  assert.equal(state.metrics.original_evidence_count, 135);
  assert.equal(state.metrics.selected_span_count, 40);
  assert.equal(state.metrics.selected_character_count, 23888);
  assert.equal(state.metrics.ai_coverage_ratio, 29.6);
  assert.equal(state.metrics.coverage_ratio, 0.2963);
  assert.equal(JSON.stringify(state.metrics).includes("报告正文"), false);
});

test("display log cap retains failures and system interpretations", () => {
  const items = Array.from({ length: LOG_LIMIT + 30 }, (_, index) => ({
    displayId: `event-${index + 1}`, sourceType: "EXECUTION_EVENT", sequence: index + 1,
    eventType: "STAGE_COMPLETED", status: "completed", details: {}, message: "ordinary",
  }));
  items.push({ displayId: "event-999", sourceType: "EXECUTION_EVENT", sequence: 999, eventType: "RULE_COMPLETED", status: "partial", details: { result: "FAIL" }, message: "failed" });
  items.push({ displayId: "insight-999", sourceType: "SYSTEM_INTERPRETATION", sequence: 999, status: "completed", details: {}, message: "safe insight" });
  const selected = selectDisplayItems(items, true);
  assert.equal(selected.items.length, LOG_LIMIT);
  assert.ok(selected.items.some((item) => item.displayId === "event-999"));
  assert.ok(selected.items.some((item) => item.displayId === "insight-999"));
  assert.equal(selected.omitted, 32);
});
