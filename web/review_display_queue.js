"use strict";

(function (root) {
  const DISPLAY_MODE_KEY = "planreview.displayMode.v1";
  const DISPLAY_MODES = Object.freeze({
    standard: Object.freeze({ targetMs: 15000, minMs: 10000, maxMs: 20000 }),
    fast: Object.freeze({ targetMs: 6500, minMs: 5000, maxMs: 8000 }),
    immediate: Object.freeze({ targetMs: 0, minMs: 0, maxMs: 0 }),
  });
  const TERMINAL = new Set(["READY_FOR_HUMAN_REVIEW", "FAILED", "INTERRUPTED"]);
  const FAILURE_TERMINAL = new Set(["FAILED", "INTERRUPTED"]);
  const SAFE_DETAIL_KEYS = new Set([
    "section_count", "span_count", "parameter_count", "normalized_fact_count",
    "applicable_rule_count", "completed_rule_count", "rule_id", "result",
    "available_span_count", "original_evidence_count", "selected_span_count",
    "selected_character_count", "coverage_ratio", "ai_coverage_ratio",
    "candidate_count", "valid_count", "rejected_count", "validation_reason_code",
    "rule_finding_count", "llm_finding_count", "final_finding_count",
    "http_status", "response_character_count", "stop_reason", "content_block_count",
  ]);

  function clamp(value, minimum, maximum) {
    return Math.max(minimum, Math.min(maximum, value));
  }

  function normalizeMode(value) {
    return Object.prototype.hasOwnProperty.call(DISPLAY_MODES, value) ? value : "standard";
  }

  function safeDetails(details) {
    const result = {};
    if (!details || typeof details !== "object" || Array.isArray(details)) return result;
    for (const [key, value] of Object.entries(details)) {
      if (!SAFE_DETAIL_KEYS.has(key)) continue;
      if (["string", "number", "boolean"].includes(typeof value) || value === null) result[key] = value;
    }
    return result;
  }

  function finiteNumber(value) {
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
  }

  function createInterpretationContext() {
    return { metrics: {}, passCount: 0, failCount: 0, unknownCount: 0 };
  }

  function updateInterpretationContext(context, event) {
    const next = {
      metrics: { ...(context?.metrics || {}), ...safeDetails(event?.details) },
      passCount: Number(context?.passCount || 0),
      failCount: Number(context?.failCount || 0),
      unknownCount: Number(context?.unknownCount || 0),
    };
    if (event?.event_type === "RULE_COMPLETED") {
      if (event.details?.result === "PASS") next.passCount += 1;
      if (event.details?.result === "FAIL") next.failCount += 1;
      if (event.details?.result === "UNKNOWN") next.unknownCount += 1;
    }
    return next;
  }

  function insight(event, key, message, details = {}) {
    return {
      displayId: `insight-${event.sequence}-${key}`,
      sourceType: "SYSTEM_INTERPRETATION",
      sequence: event.sequence,
      createdAt: event.created_at,
      stage: event.stage,
      title: "系统解读",
      message,
      status: event.status,
      details: safeDetails(details),
      syntheticDisplayOnly: true,
    };
  }

  function buildSystemInterpretations(event, context = createInterpretationContext()) {
    if (!event || !Number.isInteger(event.sequence)) return [];
    const completed = event.event_type === "STAGE_COMPLETED";
    const metrics = context.metrics || {};
    const items = [];
    if (event.stage === "DOCUMENT_PARSE" && completed && event.status === "completed") {
      const sections = finiteNumber(metrics.section_count);
      const spans = finiteNumber(metrics.span_count);
      let suffix = "";
      if (sections !== null && spans !== null) suffix = ` 当前识别 ${sections} 个章节、${spans} 个证据片段。`;
      else if (spans !== null) suffix = ` 当前识别 ${spans} 个证据片段。`;
      items.push(insight(event, "document-parse", `报告结构已完成解析，后续检查将基于可追溯证据位置进行。${suffix}`, metrics));
    }
    if (event.stage === "PARAMETER_EXTRACTION" && completed && event.status === "completed") {
      items.push(insight(event, "parameter-extraction", "已识别报告中的关键数值、单位及对应原文位置。", metrics));
    }
    if (event.stage === "PARAMETER_NORMALIZATION" && completed && event.status === "completed") {
      items.push(insight(event, "parameter-normalization", "已对关键参数的单位、类型和表达方式进行统一，便于后续规则比对。", metrics));
    }
    if (event.stage === "RULE_CONFIG" && completed && event.status === "completed") {
      const count = finiteNumber(metrics.applicable_rule_count);
      const prefix = count === null ? "本轮已加载适用规则" : `本轮已加载 ${count} 条适用规则`;
      items.push(insight(event, "rule-config", `${prefix}，将依次执行完整性、一致性和能力匹配检查。`, metrics));
    }
    if (event.event_type === "RULE_COMPLETED" && event.details?.result === "FAIL") {
      items.push(insight(event, "rule-fail", "该规则发现明确异常，相关证据将进入问题清单。", event.details));
    }
    if (event.event_type === "RULE_COMPLETED" && event.details?.result === "UNKNOWN") {
      items.push(insight(event, "rule-unknown", "当前证据不足以形成确定判断，建议由专家进一步核对。", event.details));
    }
    if (event.stage === "RULE_CHECK" && completed && context.passCount >= 1) {
      let passMessage = `通过规则 ${context.passCount} 条已完成检查`;
      if (context.failCount === 0 && context.unknownCount === 0) {
        passMessage += "，未发现新增异常。";
      } else {
        const qualifiers = [];
        if (context.failCount) qualifiers.push(`FAIL ${context.failCount} 条`);
        if (context.unknownCount) qualifiers.push(`UNKNOWN ${context.unknownCount} 条`);
        passMessage += `；另有 ${qualifiers.join("、")}，需结合问题清单核对。`;
      }
      items.push(insight(event, "pass-summary", passMessage, { result: "PASS", completed_rule_count: context.passCount }));
    }
    if (event.stage === "AI_EVIDENCE" && completed && event.status === "completed") {
      const available = finiteNumber(metrics.available_span_count ?? metrics.original_evidence_count);
      const selected = finiteNumber(metrics.selected_span_count);
      let suffix = "";
      if (available !== null && selected !== null) suffix = ` 已从 ${available} 个证据片段中选择 ${selected} 个重点片段。`;
      items.push(insight(event, "ai-evidence", `本轮 AI 优先复核规则异常、未知项及关键参数相关上下文。${suffix}`, metrics));
    }
    if (event.stage === "AI_REVIEW" && event.event_type === "STAGE_STARTED") {
      items.push(insight(event, "ai-request", "已完成结构化证据组织，正在调用已配置的 AI 服务。"));
    }
    if (event.stage === "AI_REVIEW" && event.event_type === "LLM_RESPONSE_RECEIVED") {
      items.push(insight(event, "ai-response", "AI 响应已返回，正在执行 JSON 结构、字段和证据引用校验。", event.details));
    }
    if (event.stage === "AI_REVIEW" && event.event_type === "LLM_RETRY_SCHEDULED") {
      items.push(insight(event, "ai-retry", "AI 服务首次调用发生可重试的网络异常，失败已记录，正在执行唯一一次受控重试。", event.details));
    }
    if (event.stage === "AI_REVIEW" && completed && event.status === "failed") {
      items.push(insight(event, "ai-provider-failed", "AI 服务调用失败，本次仅保留确定性规则结果。", event.details));
    }
    if (event.stage === "AI_VALIDATION" && completed && event.status === "completed") {
      const valid = finiteNumber(metrics.valid_count);
      const suffix = valid === null ? "" : `，本轮形成 ${valid} 条有效候选问题`;
      items.push(insight(event, "ai-validation", `AI 输出已通过结构与证据校验${suffix}。`, metrics));
      const available = finiteNumber(metrics.available_span_count ?? metrics.original_evidence_count);
      const selected = finiteNumber(metrics.selected_span_count);
      if (available !== null && selected !== null && selected < available) {
        items.push(insight(event, "partial-review", "AI 已复核部分重点证据，其余内容已完成确定性规则检查。", metrics));
      }
    }
    if (event.stage === "AI_VALIDATION" && completed && event.status === "failed") {
      const evidenceFailure = event.details?.validation_reason_code === "invalid_evidence";
      items.push(insight(
        event,
        evidenceFailure ? "ai-evidence-failed" : "ai-format-failed",
        evidenceFailure
          ? "AI 结果引用证据不符合要求，相关结果已丢弃。"
          : "AI 已返回内容，但未通过结构化格式校验。",
        event.details,
      ));
    }
    if (event.stage === "RECONCILIATION" && completed && event.status === "completed") {
      const total = finiteNumber(metrics.final_finding_count);
      const rules = finiteNumber(metrics.rule_finding_count);
      const ai = finiteNumber(metrics.llm_finding_count);
      let suffix = "";
      if (total !== null && rules !== null && ai !== null) suffix = ` 规则阶段产生${rules}条问题，AI形成${ai}条有效候选；融合去重后最终保留${total}条问题。`;
      items.push(insight(event, "reconciliation", `已完成规则结果与 AI 有效问题的合并、去重和来源标记。${suffix}`, metrics));
    }
    if (event.stage === "HUMAN_REVIEW" && completed && event.status === "completed") {
      items.push(insight(event, "human-review", "初审结果已准备完成，下一步由专家确认问题是否成立并填写复核意见。"));
    }
    return items;
  }

  function buildExecutionItem(event) {
    let message = String(event.message || "执行状态已更新");
    if (event.stage === "AI_REVIEW" && event.status === "failed") {
      message = "AI 服务调用失败，本次仅保留确定性规则结果";
    } else if (event.stage === "AI_VALIDATION" && event.status === "failed") {
      message = event.details?.validation_reason_code === "invalid_evidence"
        ? "AI 结果引用证据不符合要求，相关结果已丢弃"
        : "AI 已返回内容，但未通过结构化格式校验";
    }
    return {
      displayId: `event-${event.sequence}`,
      sourceType: "EXECUTION_EVENT",
      sequence: event.sequence,
      createdAt: event.created_at,
      stage: event.stage,
      eventType: event.event_type,
      title: "执行事件",
      message,
      status: event.status,
      details: safeDetails(event.details),
      syntheticDisplayOnly: false,
    };
  }

  function buildDisplayItems(events, initialContext = createInterpretationContext()) {
    let context = initialContext;
    const items = [];
    const seen = new Set();
    for (const event of [...(events || [])].sort((a, b) => a.sequence - b.sequence)) {
      if (!Number.isInteger(event.sequence) || event.sequence < 1 || seen.has(event.sequence)) continue;
      seen.add(event.sequence);
      context = updateInterpretationContext(context, event);
      items.push(buildExecutionItem(event));
      items.push(...buildSystemInterpretations(event, context));
    }
    return { items, context };
  }

  function compactPassBacklog(items, threshold = 80) {
    if ((items || []).length <= threshold) return [...(items || [])];
    const result = [];
    let batch = [];
    const flush = () => {
      if (!batch.length) return;
      if (batch.length < 2) result.push(...batch);
      else {
        const sequences = batch.map((item) => item.sequence);
        result.push({
          displayId: `pass-batch-${Math.min(...sequences)}-${Math.max(...sequences)}`,
          sourceType: "EXECUTION_EVENT",
          sequence: Math.max(...sequences),
          createdAt: batch.at(-1).createdAt,
          stage: "RULE_CHECK",
          eventType: "PASS_BATCH",
          title: "执行事件",
          message: `已完成 ${batch.length} 条通过规则事件`,
          status: "completed",
          details: { result: "PASS", completed_rule_count: batch.length },
          underlyingSequences: sequences,
          syntheticDisplayOnly: true,
        });
      }
      batch = [];
    };
    for (const item of items || []) {
      const isPass = item.sourceType === "EXECUTION_EVENT" && item.details?.result === "PASS";
      if (isPass) batch.push(item);
      else { flush(); result.push(item); }
    }
    flush();
    return result;
  }

  function itemWeight(item) {
    if (item?.sourceType === "SYSTEM_INTERPRETATION") return 2;
    if (["FAIL", "UNKNOWN"].includes(item?.details?.result) || item?.status === "failed") return 3;
    if (["AI_REVIEW", "AI_VALIDATION", "RECONCILIATION", "HUMAN_REVIEW"].includes(item?.stage)) return 2.5;
    return 1;
  }

  function computeNextDelay(mode, elapsedMs, queue, backendStatus) {
    const profile = DISPLAY_MODES[normalizeMode(mode)];
    if (profile.targetMs === 0) return 0;
    if (!TERMINAL.has(backendStatus)) {
      const first = queue?.[0];
      if (first?.sourceType === "SYSTEM_INTERPRETATION") return 550;
      if (["FAIL", "UNKNOWN"].includes(first?.details?.result)) return 750;
      if (["AI_REVIEW", "AI_VALIDATION"].includes(first?.stage)) return 650;
      if (["RECONCILIATION", "HUMAN_REVIEW"].includes(first?.stage)) return 900;
      return 180;
    }
    const remaining = clamp(profile.targetMs - elapsedMs, 0, Math.max(0, profile.maxMs - elapsedMs));
    if (!queue?.length || remaining <= 0) return 0;
    const totalWeight = queue.reduce((sum, item) => sum + itemWeight(item), 0);
    const share = remaining * itemWeight(queue[0]) / Math.max(1, totalWeight);
    return clamp(Math.round(share), 80, itemWeight(queue[0]) > 1 ? 1200 : 500);
  }

  function deriveDisplayStatus(backendStatus, queueComplete) {
    if (backendStatus === "FAILED") return "智能初审执行失败。";
    if (backendStatus === "INTERRUPTED") return "智能初审已中断。";
    if (backendStatus === "READY_FOR_HUMAN_REVIEW" && !queueComplete) return "审查已完成，正在展示完整执行过程。";
    if (backendStatus === "READY_FOR_HUMAN_REVIEW") return "智能初审任务执行完成。";
    if (backendStatus === "RUNNING") return "智能初审正在执行。";
    return "智能初审尚未开始。";
  }

  function createDisplayQueueController(options = {}) {
    const now = options.now || (() => Date.now());
    const schedule = options.setTimeout || ((callback, delay) => setTimeout(callback, delay));
    const cancel = options.clearTimeout || ((handle) => clearTimeout(handle));
    let state;
    let timer = null;
    let context = createInterpretationContext();
    let queuedIds = new Set();
    let sequences = new Set();

    function fresh(mode = "standard", flags = {}) {
      return {
        mode: normalizeMode(mode), queue: [], displayed: [], backendStatus: "IDLE",
        startedAt: null, completedAt: null, complete: false,
        finalHoldScheduled: false,
        restoring: Boolean(flags.restoring), replaying: Boolean(flags.replaying), destroyed: false,
      };
    }
    state = fresh(options.mode);

    function notify() { options.onStateChange?.(state); }
    function clearTimer() { if (timer !== null) cancel(timer); timer = null; }

    function finishIfReady() {
      if (!TERMINAL.has(state.backendStatus) || state.queue.length) return;
      const profile = DISPLAY_MODES[state.mode];
      const elapsed = state.startedAt === null ? 0 : now() - state.startedAt;
      const hold = FAILURE_TERMINAL.has(state.backendStatus) ? 0 : Math.max(0, profile.targetMs - elapsed);
      if (hold > 0 && state.mode !== "immediate" && !state.finalHoldScheduled) {
        state.finalHoldScheduled = true;
        clearTimer();
        timer = schedule(() => { timer = null; finishIfReady(); }, hold);
        return;
      }
      if (state.complete) return;
      state.complete = true;
      state.completedAt = now();
      notify();
      options.onDrain?.(state);
    }

    function displayNext() {
      timer = null;
      if (state.destroyed) return;
      if (!state.queue.length) { finishIfReady(); return; }
      const item = state.queue.shift();
      state.displayed.push(item);
      options.onDisplay?.(item, state);
      notify();
      if (state.mode === "immediate") { displayNext(); return; }
      const elapsed = state.startedAt === null ? 0 : now() - state.startedAt;
      const delay = computeNextDelay(state.mode, elapsed, state.queue, state.backendStatus);
      if (state.queue.length) timer = schedule(displayNext, delay);
      else finishIfReady();
    }

    function ensurePlaying() {
      if (state.destroyed || timer !== null || !state.queue.length) return;
      if (state.startedAt === null) state.startedAt = now();
      if (state.mode === "immediate") displayNext();
      else timer = schedule(displayNext, 0);
    }

    function enqueue(events, backendStatus = state.backendStatus) {
      state.backendStatus = backendStatus || state.backendStatus;
      const accepted = [];
      for (const event of [...(events || [])].sort((a, b) => a.sequence - b.sequence)) {
        if (!Number.isInteger(event.sequence) || event.sequence < 1 || sequences.has(event.sequence)) continue;
        sequences.add(event.sequence);
        accepted.push(event);
      }
      // Build one event at a time so display-only status notices remain adjacent to
      // their source event.  They do not participate in review state or polling.
      const staged = [];
      for (const event of accepted) {
        const built = buildDisplayItems([event], context);
        context = built.context;
        staged.push(...built.items);
        const displayOnly = options.buildDisplayOnlyItems?.(event);
        if (Array.isArray(displayOnly)) staged.push(...displayOnly);
      }
      let items = staged.filter((item) => (
        typeof item?.displayId === "string" && !queuedIds.has(item.displayId)
      ));
      for (const item of items) queuedIds.add(item.displayId);
      items = compactPassBacklog(items);
      state.queue.push(...items);
      state.complete = false;
      if (FAILURE_TERMINAL.has(state.backendStatus)) flush();
      else ensurePlaying();
      notify();
      finishIfReady();
      return items;
    }

    function setBackendStatus(status) {
      state.backendStatus = status || state.backendStatus;
      if (FAILURE_TERMINAL.has(state.backendStatus)) flush();
      notify();
      finishIfReady();
    }

    function flush() {
      clearTimer();
      state.mode = "immediate";
      state.finalHoldScheduled = false;
      if (state.startedAt === null) state.startedAt = now();
      while (state.queue.length) {
        const item = state.queue.shift();
        state.displayed.push(item);
        options.onDisplay?.(item, state);
      }
      finishIfReady();
      notify();
    }

    function setMode(mode) {
      state.mode = normalizeMode(mode);
      if (state.mode === "immediate") flush();
      else ensurePlaying();
      notify();
    }

    function reset(mode = state.mode, flags = {}) {
      clearTimer();
      state = fresh(mode, flags);
      context = createInterpretationContext();
      queuedIds = new Set();
      sequences = new Set();
      notify();
    }

    function replay(events, backendStatus, mode = state.mode) {
      reset(mode, { replaying: true });
      enqueue(events, backendStatus);
    }

    function destroy() { clearTimer(); state.destroyed = true; state.queue = []; notify(); }

    return { enqueue, setBackendStatus, setMode, flush, reset, replay, destroy, getState: () => state };
  }

  const api = {
    DISPLAY_MODE_KEY, DISPLAY_MODES, TERMINAL, normalizeMode, safeDetails,
    createInterpretationContext, updateInterpretationContext, buildSystemInterpretations,
    buildExecutionItem, buildDisplayItems, compactPassBacklog, computeNextDelay,
    deriveDisplayStatus, createDisplayQueueController,
  };
  Object.assign(root, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
