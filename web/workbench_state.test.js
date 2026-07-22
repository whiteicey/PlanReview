"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { diagnosticsAdapter, severitySummary, prioritizeFindings, safeProgressSnapshot } = require("./workbench_state.js");

test("diagnostics adapter preserves null versus zero and uses V1.2 meanings", () => {
  const view = diagnosticsAdapter(
    { final_status: "READY_FOR_HUMAN_REVIEW", llm_status: "COMPLETED_PARTIAL", finding_count: 25, llm_finding_count: 40, stages: Array(8).fill("STAGE") },
    {
      evidence_selector_version: "structured-packets-v1.2",
      integrity: { rule_result_count: 29, distinct_rule_id_count: 18, batch_count: 5 },
      packet_lifecycle_ledger: { ledger_entry_count: 504, ledger_size_bytes: 531983, ledger_truncated: false },
      ai_candidate_lifecycle_ledger: { ledger_entry_count: 0, ledger_size_bytes: 0, ledger_truncated: false },
    },
    [{ origin: "rule", severity: "high" }, { origin: "llm", severity: "low" }],
  );
  assert.equal(view.aiValidCandidateCount, 40);
  assert.equal(view.findingCount, 25);
  assert.equal(view.ruleResultCount, 29);
  assert.equal(view.distinctRuleIdCount, 18);
  assert.equal(view.batchCount, 5);
  assert.equal(view.stageRecordCount, 8);
  assert.equal(view.allStagesCompleted, null, "summary names must not imply completion");
  assert.equal(view.packetLedger.entryCount, 504);
  assert.equal(view.candidateLedger.entryCount, 0);
  assert.equal(view.originCounts.rule, 1);
  assert.equal(view.originCounts.llm, 1);
});

test("missing diagnostics stay hidden rather than becoming fabricated zeroes", () => {
  const view = diagnosticsAdapter({}, {}, []);
  assert.equal(view.ruleResultCount, null);
  assert.equal(view.batchCount, null);
  assert.equal(view.packetLedger.present, false);
  assert.equal(view.aiValidCandidateCount, null);
});

test("finding summaries are derived without duplicating business state", () => {
  const findings = [{ finding_id: "l", severity: "low" }, { finding_id: "h", severity: "high" }, { finding_id: "m", severity: "medium" }];
  assert.deepEqual(severitySummary(findings), { high: 1, medium: 1, low: 1, total: 3 });
  assert.deepEqual(prioritizeFindings(findings, 2).map((item) => item.finding_id), ["h", "m"]);
  const snapshot = safeProgressSnapshot({ runId: "run", metrics: { batch_count: 5 } });
  assert.equal(snapshot.runId, "run");
  assert.equal(snapshot.metrics.batch_count, 5);
  assert.ok(Object.isFrozen(snapshot));
});
