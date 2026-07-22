"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
  DISPLAY_MODES,
  buildDisplayItems,
  buildSystemInterpretations,
  compactPassBacklog,
  computeNextDelay,
  createDisplayQueueController,
  createInterpretationContext,
  deriveDisplayStatus,
  updateInterpretationContext,
} = require("./review_display_queue.js");

function event(sequence, stage, eventType = "STAGE_COMPLETED", status = "completed", details = {}) {
  return {
    sequence, stage, event_type: eventType, status, details,
    message: `event-${sequence}`,
    created_at: `2026-07-20T00:00:${String(sequence % 60).padStart(2, "0")}Z`,
  };
}

function fakeClock() {
  let current = 0;
  let nextId = 1;
  let tasks = [];
  return {
    now: () => current,
    setTimeout(callback, delay) {
      const id = nextId++;
      tasks.push({ id, at: current + Math.max(0, delay), callback });
      return id;
    },
    clearTimeout(id) { tasks = tasks.filter((task) => task.id !== id); },
    runAll(limit = 10000) {
      let count = 0;
      while (tasks.length) {
        if (++count > limit) throw new Error("fake clock did not settle");
        tasks.sort((a, b) => a.at - b.at || a.id - b.id);
        const task = tasks.shift();
        current = task.at;
        task.callback();
      }
      return current;
    },
    pending: () => tasks.length,
  };
}

test("display items are sorted, deduplicated and use deterministic ids", () => {
  const events = [event(3, "PARAMETER_EXTRACTION"), event(1, "DOCUMENT_PARSE"), event(3, "PARAMETER_EXTRACTION")];
  const first = buildDisplayItems(events);
  const second = buildDisplayItems(events);
  const ids = first.items.map((item) => item.displayId);
  assert.equal(ids.filter((id) => id === "event-3").length, 1);
  assert.deepEqual(ids, second.items.map((item) => item.displayId));
  assert.deepEqual(first.items.filter((item) => item.sourceType === "EXECUTION_EVENT").map((item) => item.sequence), [1, 3]);
});

test("display-only experience notices stay adjacent to the rule-config event and deduplicate", () => {
  const clock = fakeClock();
  const controller = createDisplayQueueController({
    mode: "immediate", now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    buildDisplayOnlyItems: (source) => source.stage === "RULE_CONFIG" ? [
      { displayId: "expert-experience:run-1:loading", sourceType: "DISPLAY_ONLY", message: "正在加载专家经验库" },
      { displayId: "expert-experience:run-1:loaded", sourceType: "DISPLAY_ONLY", message: "专家经验库加载完成，当前共 2 条专家经验" },
    ] : [],
  });
  controller.enqueue([event(1, "DOCUMENT_PARSE"), event(2, "RULE_CONFIG"), event(3, "RULE_CHECK")], "RUNNING");
  controller.enqueue([event(2, "RULE_CONFIG")], "RUNNING");
  const ids = controller.getState().displayed.map((item) => item.displayId);
  const ruleConfigIndex = ids.indexOf("event-2");
  const loadingIndex = ids.indexOf("expert-experience:run-1:loading");
  const loadedIndex = ids.indexOf("expert-experience:run-1:loaded");
  const ruleCheckIndex = ids.indexOf("event-3");
  assert.ok(loadingIndex > ruleConfigIndex);
  assert.equal(loadedIndex, loadingIndex + 1);
  assert.ok(ruleCheckIndex > loadedIndex);
  assert.equal(ids.filter((id) => id.startsWith("expert-experience:run-1")).length, 2);
});

test("fixed interpretations use only safe structured values", () => {
  let context = createInterpretationContext();
  const parsed = event(5, "DOCUMENT_PARSE", "STAGE_COMPLETED", "completed", {
    section_count: 18, span_count: 135,
    prompt: "SECRET_PROMPT", response: "RAW_RESPONSE", body: "SENSITIVE_BODY", path: "C:\\private",
  });
  context = updateInterpretationContext(context, parsed);
  const items = buildSystemInterpretations(parsed, context);
  assert.match(items[0].message, /18.*135/);
  const serialized = JSON.stringify(items);
  assert.doesNotMatch(serialized, /SECRET_PROMPT|RAW_RESPONSE|SENSITIVE_BODY|private/);
  assert.equal(items[0].syntheticDisplayOnly, true);
});

test("rule FAIL, UNKNOWN and PASS summary interpretations are honest", () => {
  const events = [];
  for (let index = 1; index <= 5; index += 1) events.push(event(index, "RULE_CHECK", "RULE_COMPLETED", "completed", { rule_id: `R${index}`, result: "PASS" }));
  events.push(event(6, "RULE_CHECK", "RULE_COMPLETED", "partial", { rule_id: "RF", result: "FAIL" }));
  events.push(event(7, "RULE_CHECK", "RULE_COMPLETED", "partial", { rule_id: "RU", result: "UNKNOWN" }));
  events.push(event(8, "RULE_CHECK", "STAGE_COMPLETED", "completed", { applicable_rule_count: 7, completed_rule_count: 7 }));
  const messages = buildDisplayItems(events).items.filter((item) => item.sourceType === "SYSTEM_INTERPRETATION").map((item) => item.message);
  assert.ok(messages.some((message) => message.includes("明确异常")));
  assert.ok(messages.some((message) => message.includes("证据不足")));
  assert.ok(messages.some((message) => message.includes("通过规则 5 条")));
  assert.ok(messages.some((message) => message.includes("FAIL 1 条") && message.includes("UNKNOWN 1 条")));
  assert.ok(messages.every((message) => !message.includes("未发现新增异常。") || message.includes("通过规则 5 条")));
});

test("AI success, partial coverage and two validation failures are distinct", () => {
  const evidence = event(1, "AI_EVIDENCE", "STAGE_COMPLETED", "completed", { available_span_count: 135, selected_span_count: 40 });
  const validation = event(2, "AI_VALIDATION", "STAGE_COMPLETED", "completed", { valid_count: 6 });
  let context = updateInterpretationContext(createInterpretationContext(), evidence);
  context = updateInterpretationContext(context, validation);
  const success = buildSystemInterpretations(validation, context).map((item) => item.message).join(" ");
  assert.match(success, /6 条有效候选问题/);
  assert.match(success, /部分重点证据/);
  const format = buildSystemInterpretations(event(3, "AI_VALIDATION", "STAGE_COMPLETED", "failed", { validation_reason_code: "invalid_json" }), context)[0].message;
  const evidenceFailure = buildSystemInterpretations(event(4, "AI_VALIDATION", "STAGE_COMPLETED", "failed", { validation_reason_code: "invalid_evidence" }), context)[0].message;
  assert.match(format, /格式校验/);
  assert.match(evidenceFailure, /引用证据/);
});

test("provider failure and its single controlled retry use distinct safe interpretations", () => {
  const retry = buildSystemInterpretations(event(5, "AI_REVIEW", "LLM_RETRY_SCHEDULED", "partial", { retry_attempt: 1, provider_error_code: "timeout" }), createInterpretationContext())[0].message;
  const failed = buildSystemInterpretations(event(6, "AI_REVIEW", "STAGE_COMPLETED", "failed", { provider_error_code: "timeout" }), createInterpretationContext())[0].message;
  assert.match(retry, /唯一一次受控重试/);
  assert.equal(failed, "AI 服务调用失败，本次仅保留确定性规则结果。");
});

test("historical generic AI failure events are rendered with their true safe category", () => {
  const formatEvent = event(7, "AI_VALIDATION", "STAGE_COMPLETED", "failed", { validation_reason_code: "truncated_json" });
  formatEvent.message = "AI 输出格式校验失败，AI 结果已丢弃";
  const evidenceEvent = event(8, "AI_VALIDATION", "STAGE_COMPLETED", "failed", { validation_reason_code: "invalid_evidence" });
  const providerEvent = event(9, "AI_REVIEW", "STAGE_COMPLETED", "failed", { provider_error_code: "timeout" });
  const executionMessages = buildDisplayItems([formatEvent, evidenceEvent, providerEvent]).items
    .filter((item) => item.sourceType === "EXECUTION_EVENT")
    .map((item) => item.message);
  assert.deepEqual(executionMessages, [
    "AI 已返回内容，但未通过结构化格式校验",
    "AI 结果引用证据不符合要求，相关结果已丢弃",
    "AI 服务调用失败，本次仅保留确定性规则结果",
  ]);
});

test("interpretations never claim model thinking or create findings", () => {
  const history = [
    event(1, "AI_REVIEW", "STAGE_STARTED", "running"),
    event(2, "AI_REVIEW", "LLM_RESPONSE_RECEIVED", "completed", { candidate_count: 3 }),
    event(3, "RECONCILIATION", "STAGE_COMPLETED", "completed", { rule_finding_count: 12, llm_finding_count: 3, final_finding_count: 15 }),
  ];
  const built = buildDisplayItems(history).items;
  const text = built.filter((item) => item.sourceType === "SYSTEM_INTERPRETATION").map((item) => item.message).join(" ");
  assert.doesNotMatch(text, /正在思考|思维链|深入理解|像专家一样推理/);
  assert.ok(built.filter((item) => item.sourceType === "SYSTEM_INTERPRETATION").every((item) => !Object.hasOwn(item, "finding")));
  assert.match(text, /规则阶段产生12条问题，AI形成3条有效候选；融合去重后最终保留15条问题。/);
});

test("large PASS backlog is compacted without dropping critical events", () => {
  const items = [];
  for (let index = 1; index <= 90; index += 1) {
    items.push({ displayId: `event-${index}`, sourceType: "EXECUTION_EVENT", sequence: index, stage: "RULE_CHECK", eventType: "RULE_COMPLETED", status: "completed", details: { result: "PASS" } });
  }
  items.push({ displayId: "event-91", sourceType: "EXECUTION_EVENT", sequence: 91, stage: "RULE_CHECK", eventType: "RULE_COMPLETED", status: "partial", details: { result: "FAIL" } });
  const compacted = compactPassBacklog(items);
  assert.ok(compacted.length < items.length);
  assert.ok(compacted.some((item) => item.displayId === "event-91"));
  assert.equal(compacted.find((item) => item.eventType === "PASS_BATCH").underlyingSequences.length, 90);
});

for (const [mode, minimum, maximum] of [["standard", 10000, 20000], ["fast", 5000, 8000]]) {
  test(`${mode} mode completes inside its normal duration`, () => {
    const clock = fakeClock();
    let drained = 0;
    const controller = createDisplayQueueController({
      mode, now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
      onDrain: () => { drained += 1; },
    });
    const history = Array.from({ length: 12 }, (_, index) => event(index + 1, index === 11 ? "HUMAN_REVIEW" : "PARAMETER_EXTRACTION", "STAGE_COMPLETED", "completed"));
    controller.enqueue(history, "READY_FOR_HUMAN_REVIEW");
    const elapsed = clock.runAll();
    assert.ok(elapsed >= minimum && elapsed <= maximum, `${elapsed} outside ${minimum}-${maximum}`);
    assert.equal(drained, 1);
    assert.equal(controller.getState().complete, true);
  });
}

test("immediate mode drains now and future events without delay", () => {
  const clock = fakeClock();
  const controller = createDisplayQueueController({ mode: "immediate", now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  controller.enqueue([event(1, "DOCUMENT_PARSE")], "RUNNING");
  assert.equal(controller.getState().queue.length, 0);
  controller.enqueue([event(2, "HUMAN_REVIEW")], "READY_FOR_HUMAN_REVIEW");
  assert.equal(controller.getState().complete, true);
  assert.equal(clock.pending(), 0);
});

test("flush cancels the remaining presentation hold without changing stored preference", () => {
  const clock = fakeClock();
  let drained = 0;
  const controller = createDisplayQueueController({
    mode: "standard", now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    onDrain: () => { drained += 1; },
  });
  controller.enqueue([event(1, "DOCUMENT_PARSE"), event(2, "HUMAN_REVIEW")], "READY_FOR_HUMAN_REVIEW");
  controller.flush();
  assert.equal(controller.getState().mode, "immediate");
  assert.equal(controller.getState().complete, true);
  assert.equal(clock.pending(), 0);
  assert.equal(drained, 1);
});

test("failure flushes immediately and long-running backend is never faked complete", () => {
  assert.equal(deriveDisplayStatus("RUNNING", false), "智能初审正在执行。");
  assert.equal(deriveDisplayStatus("READY_FOR_HUMAN_REVIEW", false), "审查已完成，正在展示完整执行过程。");
  assert.equal(deriveDisplayStatus("FAILED", false), "智能初审执行失败。");
  const delay = computeNextDelay("standard", DISPLAY_MODES.standard.maxMs + 1000, [buildDisplayItems([event(1, "HUMAN_REVIEW")]).items[0]], "RUNNING");
  assert.ok(delay > 0);
  const clock = fakeClock();
  const controller = createDisplayQueueController({ mode: "standard", now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  controller.enqueue([event(1, "AI_REVIEW", "STAGE_COMPLETED", "failed")], "FAILED");
  assert.equal(controller.getState().complete, true);
  assert.equal(clock.pending(), 0);
});

test("reset, replay and destroy clear timers and preserve no duplicate sequence", () => {
  const clock = fakeClock();
  const controller = createDisplayQueueController({ mode: "standard", now: clock.now, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout });
  controller.enqueue([event(1, "DOCUMENT_PARSE"), event(1, "DOCUMENT_PARSE")], "RUNNING");
  assert.ok(clock.pending() > 0);
  controller.reset("standard");
  assert.equal(clock.pending(), 0);
  controller.replay([event(1, "DOCUMENT_PARSE")], "READY_FOR_HUMAN_REVIEW", "immediate");
  assert.equal(controller.getState().displayed.filter((item) => item.displayId === "event-1").length, 1);
  controller.destroy();
  assert.equal(clock.pending(), 0);
});
