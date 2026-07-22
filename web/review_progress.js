"use strict";

(function (root) {
  const displayApi = root.createDisplayQueueController
    ? root
    : (typeof require !== "undefined" ? require("./review_display_queue.js") : {});
  const STORAGE_KEY = "planreview.activeRun.v1";
  const LOG_LIMIT = 400;
  const TERMINAL = new Set(["READY_FOR_HUMAN_REVIEW", "FAILED", "INTERRUPTED"]);
  const STAGES = Object.freeze([
    ["INPUT_VALIDATION", "输入校验", 5],
    ["DOCUMENT_PARSE", "文档解析", 15],
    ["PARAMETER_EXTRACTION", "参数提取", 12],
    ["PARAMETER_NORMALIZATION", "参数规范化", 8],
    ["RULE_CONFIG", "规则配置加载", 5],
    ["RULE_CHECK", "确定性规则检查", 25],
    ["AI_EVIDENCE", "AI 证据准备", 8],
    ["AI_REVIEW", "AI 复核", 8],
    ["AI_VALIDATION", "AI 输出校验", 6],
    ["RECONCILIATION", "结果融合", 5],
    ["HUMAN_REVIEW", "等待专家复核", 3],
  ]);
  const STAGE_MAP = Object.fromEntries(STAGES.map(([id, label, weight]) => [id, { label, weight }]));

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[ch]));
  }

  function createProgressState() {
    return {
      runStatus: "IDLE", lastSequence: 0, events: [], eventSequences: new Set(),
      stageStatus: {}, metrics: {}, progress: 0, currentStage: null, currentMessage: "尚未开始审查",
    };
  }

  function rebuild(state) {
    state.stageStatus = {};
    state.metrics = {};
    state.currentStage = null;
    state.currentMessage = "审查任务已创建，正在等待后台执行记录。";
    for (const event of state.events) {
      const details = event.details || {};
      if (STAGE_MAP[event.stage]) {
        if (event.event_type === "STAGE_STARTED") state.stageStatus[event.stage] = "running";
        if (event.event_type === "STAGE_COMPLETED") state.stageStatus[event.stage] = event.status;
        if (["STAGE_STARTED", "STAGE_COMPLETED"].includes(event.event_type)) state.currentStage = event.stage;
      }
      Object.assign(state.metrics, details);
      if (["STAGE_STARTED", "RULE_STARTED"].includes(event.event_type) || event.status === "running") {
        state.currentStage = event.stage;
      }
      state.currentMessage = event.message;
    }
    let value = 0;
    for (const [id, , weight] of STAGES) {
      const stageStatus = state.stageStatus[id];
      if (["completed", "skipped", "failed", "partial"].includes(stageStatus)) {
        value += weight;
      } else if (id === "RULE_CHECK") {
        const total = Number(state.metrics.applicable_rule_count || 0);
        const completed = Number(state.metrics.completed_rule_count || 0);
        if (total > 0) value += weight * Math.min(1, completed / total);
      }
    }
    if (state.runStatus === "READY_FOR_HUMAN_REVIEW") value = 100;
    if (["FAILED", "INTERRUPTED"].includes(state.runStatus)) value = Math.min(99, value);
    state.progress = Math.max(0, Math.min(100, Math.round(value)));
    return state;
  }

  function applyProgressBatch(state, payload) {
    for (const event of [...(payload.events || [])].sort((a, b) => a.sequence - b.sequence)) {
      if (!Number.isInteger(event.sequence) || event.sequence < 1 || state.eventSequences.has(event.sequence)) continue;
      state.eventSequences.add(event.sequence);
      state.events.push(event);
    }
    state.events.sort((a, b) => a.sequence - b.sequence);
    state.lastSequence = Math.max(state.lastSequence, Number(payload.last_sequence || 0));
    state.runStatus = payload.run_status || state.runStatus;
    return rebuild(state);
  }

  function selectLogEvents(events, showPass = false, limit = LOG_LIMIT) {
    const passRuleIds = new Set(events.filter((event) => event.event_type === "RULE_COMPLETED" && event.details?.result === "PASS").map((event) => event.details.rule_id));
    const filtered = events.filter((event) => {
      if (showPass) return true;
      return !(event.details?.result === "PASS" || (event.event_type === "RULE_STARTED" && passRuleIds.has(event.details?.rule_id)));
    });
    if (filtered.length <= limit) return { events: filtered, omitted: events.length - filtered.length };
    const priority = (event) => {
      if (event.status === "failed" || ["FAIL", "UNKNOWN"].includes(event.details?.result)) return 3;
      if (event.status === "running") return 2;
      return 1;
    };
    const selected = [...filtered]
      .sort((a, b) => priority(b) - priority(a) || b.sequence - a.sequence)
      .slice(0, limit)
      .sort((a, b) => a.sequence - b.sequence);
    return { events: selected, omitted: events.length - selected.length };
  }

  function formatElapsed(createdAt, finishedAt) {
    if (!createdAt) return "00:00";
    const seconds = Math.max(0, Math.floor((new Date(finishedAt || Date.now()) - new Date(createdAt)) / 1000));
    return `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
  }

  function selectDisplayItems(items, showPass = false, limit = LOG_LIMIT) {
    const passRuleIds = new Set((items || [])
      .filter((item) => item.sourceType === "EXECUTION_EVENT" && item.eventType === "RULE_COMPLETED" && item.details?.result === "PASS")
      .map((item) => item.details?.rule_id));
    const filtered = (items || []).filter((item) => {
      if (showPass || item.sourceType === "SYSTEM_INTERPRETATION") return true;
      if (item.details?.result === "PASS") return false;
      return !(item.eventType === "RULE_STARTED" && passRuleIds.has(item.details?.rule_id));
    });
    if (filtered.length <= limit) return { items: filtered, omitted: (items || []).length - filtered.length };
    const priority = (item) => {
      if (item.status === "failed" || ["FAIL", "UNKNOWN"].includes(item.details?.result)) return 4;
      if (item.sourceType === "SYSTEM_INTERPRETATION") return 3;
      if (item.status === "running") return 2;
      return 1;
    };
    const selected = [...filtered]
      .sort((a, b) => priority(b) - priority(a) || b.sequence - a.sequence)
      .slice(0, limit)
      .sort((a, b) => a.sequence - b.sequence || a.displayId.localeCompare(b.displayId));
    return { items: selected, omitted: (items || []).length - selected.length };
  }

  function createReviewProgressController(element, options = {}) {
    let state = createProgressState();
    let visibleState = createProgressState();
    let caseId = null;
    let runId = null;
    let timer = null;
    let elapsedTimer = null;
    let autoScroll = true;
    let showPass = false;
    let terminalNotified = false;
    let displayHistory = [];
    let displayQueue = null;
    let preferredMode = displayApi.normalizeMode?.(localStorage.getItem(displayApi.DISPLAY_MODE_KEY)) || "standard";
    let restoreSession = false;
    let shellReady = false;
    let reducedMotion = Boolean(root.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);

    const role = (name) => element.querySelector(`[data-role="${name}"]`);
    const metric = (value, locale = false) => {
      if (value === null || value === undefined) return "—";
      const numeric = Number(value);
      return Number.isFinite(numeric) ? (locale ? numeric.toLocaleString("zh-CN") : String(numeric)) : "—";
    };

    function ensureShell() {
      if (shellReady) return;
      element.innerHTML = `
        <section class="review-progress-panel" aria-label="智能初审执行台">
          <header class="review-progress-header">
            <div><p class="eyebrow">AI REVIEW EXECUTION</p><h2>智能初审执行台</h2><p class="review-progress-status"><span data-role="status"></span> · 已用时 <span data-role="elapsed">00:00</span></p></div>
            <span class="review-progress-run" data-role="run">Run —</span>
          </header>
          <div class="review-progress-current"><strong data-role="current-stage">当前阶段：等待启动</strong><span data-role="current-message">尚未开始审查</span></div>
          <div class="review-progress-bar" data-role="bar" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"><i></i></div><div class="review-progress-percent" data-role="percent">0%</div>
          <div class="review-progress-metrics">
            <span>章节<b data-metric="section_count">—</b></span><span>完整解析片段<b data-metric="span_count">—</b></span><span>原始证据片段<b data-metric="available_span_count">—</b></span><span>实际送审片段<b data-metric="selected_span_count">—</b></span><span>送审字符<b data-metric="selected_character_count">—</b></span><span>AI覆盖比例<b data-metric="coverage_ratio">—</b></span><span>提取参数<b data-metric="parameter_count">—</b></span><span>执行规则<b data-metric="rule_count">—/—</b></span><span>AI候选问题<b data-metric="candidate_count">—</b></span><span>最终问题<b data-metric="final_finding_count">—</b></span>
          </div>
          <p class="review-progress-partial-note" data-role="partial-note" hidden>AI已复核部分重点证据，其余内容已完成确定性规则检查</p>
          <div class="review-progress-playback-tools">
            <label>展示速度<select data-role="mode"><option value="standard">标准（10–20秒）</option><option value="fast">快速（5–8秒）</option><option value="immediate">立即显示</option></select></label>
            <button type="button" data-role="flush">立即显示全部</button><button type="button" data-role="view-results" hidden>立即查看结果</button><button type="button" data-role="replay" disabled>回放执行过程</button>
          </div>
          <p class="review-progress-interpretation-note">系统解读根据真实执行数据自动生成，不代表模型内部思维过程。</p>
          <div class="review-progress-body"><ol class="review-progress-timeline" data-role="timeline"></ol><div class="review-progress-log-wrap"><div class="review-progress-log-tools"><strong>动态执行日志</strong><label><input type="checkbox" data-role="show-pass">显示已通过规则</label></div><p class="review-progress-omitted" data-role="omitted" hidden></p><div class="review-progress-log" data-role="log"><p class="review-progress-waiting" data-role="waiting">审查任务已创建，正在等待后台执行记录。</p></div><button type="button" class="review-progress-new-logs" data-role="new-logs" hidden>有新日志</button></div></div>
        </section>`;
      role("timeline").append(...STAGES.map(([id, label]) => {
        const item = document.createElement("li");
        item.className = "review-progress-stage review-progress-stage-pending";
        item.dataset.stage = id;
        const symbol = document.createElement("span"); symbol.textContent = "○";
        const text = document.createElement("span"); text.textContent = label;
        const small = document.createElement("small"); small.textContent = "等待";
        item.append(symbol, text, small); return item;
      }));
      role("mode").value = preferredMode;
      role("mode").addEventListener("change", (event) => {
        preferredMode = displayApi.normalizeMode(event.target.value);
        localStorage.setItem(displayApi.DISPLAY_MODE_KEY, preferredMode);
        restoreSession = false;
        displayQueue?.setMode(reducedMotion ? "immediate" : preferredMode);
      });
      role("flush").addEventListener("click", () => displayQueue?.flush());
      role("view-results").addEventListener("click", () => displayQueue?.flush());
      role("replay").addEventListener("click", replay);
      role("show-pass").addEventListener("change", (event) => { showPass = event.target.checked; rebuildLogs(); });
      role("new-logs").addEventListener("click", () => {
        autoScroll = true; role("log").scrollTop = role("log").scrollHeight; role("new-logs").hidden = true;
      });
      role("log").addEventListener("scroll", () => {
        autoScroll = role("log").scrollHeight - role("log").scrollTop - role("log").clientHeight < 24;
        if (autoScroll) role("new-logs").hidden = true;
      });
      shellReady = true;
    }

    function createLogNode(item) {
      const node = document.createElement("div");
      node.className = `review-progress-event review-progress-event-${item.sourceType === "SYSTEM_INTERPRETATION" ? "interpretation" : "execution"}`;
      node.dataset.displayId = item.displayId;
      const time = document.createElement("time");
      const parsed = new Date(item.createdAt);
      time.textContent = Number.isNaN(parsed.getTime()) ? "--:--:--" : parsed.toLocaleTimeString("zh-CN", { hour12: false });
      const stage = document.createElement("span"); stage.className = "review-progress-event-stage"; stage.textContent = STAGE_MAP[item.stage]?.label || item.stage || "执行";
      const source = document.createElement("span"); source.className = "review-progress-event-source"; source.textContent = item.sourceType === "SYSTEM_INTERPRETATION" ? "系统解读" : "执行事件";
      const message = document.createElement("p"); message.textContent = item.message;
      node.append(time, stage, source, message);
      return node;
    }

    function rebuildLogs() {
      ensureShell();
      const selected = selectDisplayItems(displayHistory, showPass);
      const log = role("log"); log.replaceChildren();
      const fragment = document.createDocumentFragment();
      for (const item of selected.items) fragment.append(createLogNode(item));
      if (!selected.items.length) {
        const waiting = document.createElement("p"); waiting.className = "review-progress-waiting"; waiting.textContent = "审查任务已创建，正在等待后台执行记录。"; fragment.append(waiting);
      }
      log.append(fragment);
      role("omitted").hidden = !selected.omitted;
      role("omitted").textContent = selected.omitted ? `已折叠或省略 ${selected.omitted} 条历史记录` : "";
      if (autoScroll) log.scrollTop = log.scrollHeight;
    }

    function appendLog(item) {
      const passChanged = item.sourceType === "EXECUTION_EVENT" && item.eventType === "RULE_COMPLETED" && item.details?.result === "PASS";
      const selected = selectDisplayItems(displayHistory, showPass);
      if (passChanged || selected.items.length >= LOG_LIMIT || role("log").querySelector(".review-progress-waiting")) { rebuildLogs(); return; }
      if (!selected.items.some((candidate) => candidate.displayId === item.displayId)) return;
      const fragment = document.createDocumentFragment(); fragment.append(createLogNode(item)); role("log").append(fragment);
      if (autoScroll) role("log").scrollTop = role("log").scrollHeight;
      else role("new-logs").hidden = false;
    }

    function exposeEvent(item) {
      const sequences = item.underlyingSequences || (item.sourceType === "EXECUTION_EVENT" ? [item.sequence] : []);
      const events = sequences.map((sequence) => state.events.find((event) => event.sequence === sequence)).filter(Boolean);
      if (events.length) applyProgressBatch(visibleState, { events, last_sequence: Math.max(...sequences), run_status: "RUNNING" });
    }

    function updateSummary() {
      ensureShell();
      const queueState = displayQueue?.getState() || { complete: false, queue: [] };
      const first = state.events[0]?.created_at;
      const last = TERMINAL.has(state.runStatus) ? state.events.at(-1)?.created_at : null;
      role("status").textContent = displayApi.deriveDisplayStatus(state.runStatus, queueState.complete);
      role("elapsed").textContent = formatElapsed(first, last);
      role("run").textContent = `Run ${runId ? runId.slice(0, 8) : "—"}`;
      role("current-stage").textContent = `当前阶段：${STAGE_MAP[visibleState.currentStage]?.label || "等待启动"}`;
      role("current-message").textContent = visibleState.currentMessage;
      const progress = queueState.complete && state.runStatus === "READY_FOR_HUMAN_REVIEW" ? 100 : visibleState.progress;
      role("bar").setAttribute("aria-valuenow", String(progress)); role("bar").querySelector("i").style.width = `${progress}%`; role("percent").textContent = `${progress}%`;
      for (const node of role("timeline").children) {
        const status = visibleState.stageStatus[node.dataset.stage] || "pending";
        node.className = `review-progress-stage review-progress-stage-${status}`;
        node.firstElementChild.textContent = status === "completed" ? "✓" : status === "running" ? "●" : status === "failed" ? "!" : status === "partial" ? "◐" : status === "skipped" ? "–" : "○";
        node.lastElementChild.textContent = ({ pending: "待执行", running: "执行中", completed: "已完成", failed: "失败", partial: "部分完成", skipped: "已跳过" })[status] || status;
      }
      const metrics = visibleState.metrics;
      const setMetric = (name, value) => { const node = element.querySelector(`[data-metric="${name}"]`); if (node) node.textContent = value; };
      setMetric("section_count", metric(metrics.section_count));
      setMetric("span_count", metric(metrics.span_count));
      setMetric("available_span_count", metric(metrics.available_span_count ?? metrics.original_evidence_count));
      setMetric("selected_span_count", metric(metrics.selected_span_count));
      setMetric("selected_character_count", metric(metrics.selected_character_count, true));
      const rawCoverage = metrics.coverage_ratio;
      const coverage = rawCoverage === null || rawCoverage === undefined ? metrics.ai_coverage_ratio : Number(rawCoverage) * 100;
      setMetric("coverage_ratio", Number.isFinite(Number(coverage)) ? `${Number(coverage).toFixed(1)}%` : "—");
      setMetric("parameter_count", metric(metrics.parameter_count ?? metrics.normalized_fact_count));
      setMetric("rule_count", `${metric(metrics.completed_rule_count)}/${metric(metrics.applicable_rule_count)}`);
      setMetric("candidate_count", metric(metrics.candidate_count)); setMetric("final_finding_count", metric(metrics.final_finding_count));
      role("partial-note").hidden = !(Number(rawCoverage) < 1 && visibleState.events.some((event) => event.stage === "AI_VALIDATION" && event.event_type === "STAGE_COMPLETED" && event.status === "completed"));
      role("flush").disabled = !queueState.queue?.length;
      role("view-results").hidden = !(state.runStatus === "READY_FOR_HUMAN_REVIEW" && !queueState.complete);
      role("replay").disabled = !(TERMINAL.has(state.runStatus) && queueState.complete && state.events.length);
      options.onStateChange?.(root.safeProgressSnapshot ? root.safeProgressSnapshot({
        caseId, runId, runStatus: state.runStatus, currentStage: visibleState.currentStage,
        currentMessage: visibleState.currentMessage, progress, elapsed: formatElapsed(first, last),
        displayComplete: Boolean(queueState.complete), metrics,
      }) : {
        caseId, runId, runStatus: state.runStatus, currentStage: visibleState.currentStage,
        currentMessage: visibleState.currentMessage, progress, elapsed: formatElapsed(first, last),
        displayComplete: Boolean(queueState.complete), metrics: { ...metrics },
      });
    }

    function onDisplay(item) {
      exposeEvent(item); displayHistory.push(item); appendLog(item); updateSummary();
    }

    function notifyTerminal() {
      if (terminalNotified || !TERMINAL.has(state.runStatus)) return;
      terminalNotified = true;
      if (elapsedTimer) { clearInterval(elapsedTimer); elapsedTimer = null; }
      options.onTerminal?.(state, caseId, runId);
    }

    function makeDisplayQueue(mode) {
      displayQueue?.destroy();
      displayQueue = displayApi.createDisplayQueueController({
        mode,
        buildDisplayOnlyItems: (event) => options.buildDisplayOnlyItems?.(event, caseId, runId) || [],
        onDisplay,
        onStateChange: updateSummary,
        onDrain: () => { visibleState.runStatus = state.runStatus; rebuild(visibleState); updateSummary(); notifyTerminal(); },
      });
    }

    function clearDisplay() {
      visibleState = createProgressState(); displayHistory = []; autoScroll = true; rebuildLogs(); updateSummary();
    }

    function replay() {
      if (!TERMINAL.has(state.runStatus) || !state.events.length) return;
      terminalNotified = false; options.onPlaybackStart?.(caseId, runId, true); clearDisplay();
      const mode = reducedMotion ? "immediate" : preferredMode;
      makeDisplayQueue(mode); displayQueue.replay(state.events, state.runStatus, mode);
    }

    async function poll() {
      if (!runId || TERMINAL.has(state.runStatus)) return;
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(runId)}/progress?after_sequence=${state.lastSequence}`);
        if (response.status === 404) {
          localStorage.removeItem(STORAGE_KEY); reset(); return;
        }
        if (response.ok) {
          const payload = await response.json();
          const before = new Set(state.eventSequences);
          applyProgressBatch(state, payload);
          const newEvents = (payload.events || []).filter((event) => !before.has(event.sequence) && state.eventSequences.has(event.sequence));
          displayQueue.enqueue(newEvents, state.runStatus);
        }
        updateSummary();
        if (TERMINAL.has(state.runStatus)) {
          if (timer) clearTimeout(timer); timer = null;
          displayQueue.setBackendStatus(state.runStatus);
        }
      } finally {
        if (!TERMINAL.has(state.runStatus)) timer = setTimeout(poll, 800);
      }
    }

    function stop() { if (timer) clearTimeout(timer); if (elapsedTimer) clearInterval(elapsedTimer); displayQueue?.destroy(); timer = null; elapsedTimer = null; }
    function reset() { stop(); state = createProgressState(); visibleState = createProgressState(); displayHistory = []; caseId = null; runId = null; terminalNotified = false; makeDisplayQueue(preferredMode); rebuildLogs(); updateSummary(); }
    function start(nextCaseId, nextRunId, restore = false) {
      stop(); ensureShell(); state = createProgressState(); visibleState = createProgressState(); state.runStatus = "RUNNING"; caseId = nextCaseId; runId = nextRunId; terminalNotified = false; restoreSession = Boolean(restore); displayHistory = [];
      const mode = reducedMotion || restoreSession ? "immediate" : preferredMode;
      makeDisplayQueue(mode); localStorage.setItem(STORAGE_KEY, JSON.stringify({ caseId, runId })); rebuildLogs(); updateSummary();
      options.onPlaybackStart?.(caseId, runId, false); elapsedTimer = setInterval(updateSummary, 1000); poll();
      if (!restore) options.onStarted?.(caseId, runId);
    }
    function restore() {
      try { const saved = JSON.parse(localStorage.getItem(STORAGE_KEY)); if (saved?.caseId && saved?.runId) { start(saved.caseId, saved.runId, true); return true; } } catch (_) { localStorage.removeItem(STORAGE_KEY); }
      updateSummary(); return false;
    }
    ensureShell(); makeDisplayQueue(preferredMode); updateSummary();
    return { start, restore, reset, stop, replay, flush: () => displayQueue.flush(), getState: () => state, getDisplayState: () => displayQueue.getState() };
  }

  const api = { STAGES, LOG_LIMIT, escapeHtml, createProgressState, applyProgressBatch, selectLogEvents, selectDisplayItems, createReviewProgressController };
  Object.assign(root, api);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
