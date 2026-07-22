# DEMO golden status deviation

This project's checked-in golden mirror (`tests/golden/golden_cases_demo.expected.jsonl`)
deviates from the bundled DEMO oracle (`本地版示例数据包/golden/golden_cases_demo.jsonl`)
in the ways recorded below. Every deviation is an honesty correction: the mirror
asserts only what the generic, first-principles engine actually produces from real
facts, SourceSpans, and structure — never a value tuned to a removed shortcut.

## 1. Three-valued status contract (D3)

The bundled package uses two extended status labels outside this project's
three-valued `RuleStatus` (PASS / FAIL / UNKNOWN):

- `VERSION-001: SUSPECTED` → represented as `FAIL`/`UNKNOWN` per parameter with a
  declarative `requires_human_review` flag (derived generically from the external
  rule's `on_missing: suspected`, not a rule-ID branch).
- `EVIDENCE-001: BLOCK` → represented as `UNKNOWN` with `details.blocked: true` and a
  human-review Finding.

## 2. G-007 operator correction

The original bundled line used `CONSISTENCY-001` for a unit-conversion scenario, but
its facts are `开发井总数=2`, `单井设计产能=5万m³/d`, `总设计产能=100000 m³/d`. The
mirror uses `CONSISTENCY-003=PASS`, because `product_approximately_equals` is the
coherent rule for convertible units after normalization.

## 3. Removal of the prose-grep compatibility layer (2026-07-15)

An earlier baseline reached a passing golden by grepping DEMO document prose for verdict
trigger strings (e.g. `建设周期冲突`) and injecting synthetic rules via rule-ID branches.
That layer was removed in full (guarded by `test_compatibility_safety.py`). The expected
values were re-derived from the honest engine output, which differs from the
legacy-calibrated originals as follows.

### 3.1 Cross-parameter operators no longer require operands to share scope

`sum_equals`, `product_approximately_equals`, and `less_or_equal` compare *different*
physical quantities that naturally live in different scopes (a well count is a
build-phase figure; a processing capacity is a design figure). They now require each
operand to resolve to one complete-key, single-valued fact, but do **not** require the
different operands to share `time_scope`/`statistical_scope`. Without this fix the clean
baseline DEMO-001 returned UNKNOWN on these rules and produced spurious findings. Each
operand's own comparison key must still be complete (spec §参数比较键).

### 3.2 `change_requires_reason` passes on a single-version document

A single-version document has no cross-version change to explain, so VERSION-001 now
yields PASS for a parameter that appears in only one version with a consistent value
(previously UNKNOWN → spurious finding). UNKNOWN is reserved for genuinely
missing/ambiguous data or intra-version value conflicts.

### 3.3 `issue_response_status_exists` treats a non-empty status as present

Rule 9 ("上一轮意见有回复状态") is an existence check. `待回复` (awaiting reply) is a
valid status, so a non-empty status cell counts as present even when it is not one of the
enumerated closed states (`待整改/整改中/已整改/已闭环`). Missing/blank status cells are
the concern of the repo-owned `COMPLETENESS-003` (reply-completeness). This distinction
keeps the clean baseline (DEMO-001/002/003-V1, all statuses `待回复`) at PASS while
DEMO-004 (one blank status cell) fails COMPLETENESS-003.

## 4. Repo-owned generic rules

Two checks the authoritative 10-rule external set cannot express are added as
repo-owned rules in `app/rules/repo_rules.yaml` (versioned `0.1.0-repo`, distinct from the
external `0.1.0-demo`):

- `COMPLETENESS-003` (`reply_table_status_complete`): a review-opinion table data row with
  a blank status cell fails. Catches DEMO-004's `DEMO-OP-001` empty-status row. Verified
  silent on DEMO-001/003 (all rows filled).
- `TERM-002` (`prose_alias_unnormalized`): a *distinct* alias term used in body prose
  instead of the canonical name fails. Catches DEMO-002 (`钻井总数`) and DEMO-004
  (`部署井数`/`井位数量`). Aliases that are substrings of their own canonical
  (`生产井`⊂`生产井数`) are generic words and ignored, so the clean baseline stays silent.

## 5. Findings the oracle expects that the honest engine does NOT produce

These were previously "caught" only by the prose-grep cheat. They are dropped from the
mirror and documented here rather than faked:

- **建设周期 / 首次投产时间 single-document temporal contradiction** (DEMO-002/DEMO-004).
  Measured: a generic `方案日期 + 建设周期 > 首次投产时间` rule fires on **all five docs
  including the clean baseline DEMO-001** (完工=24337 > 投产=24330 for DEMO-001), so no
  honest generic rule can flag it without breaking the mandatory G-001 = 0-findings
  baseline. The real defect is a temporal-logic relationship, not a value inconsistency,
  and 方案日期 is not currently extracted as a comparable fact. Deferred, not faked. (On
  G-003, 建设周期/首次投产时间 findings DO appear honestly via VERSION-001's cross-version
  fan-out, which is a different mechanism.)
- **年度产量 unknown_scope** (DEMO-002). A generic "missing statistical_scope ⇒ flag" rule
  fires on most body-extracted facts in every document including DEMO-001, so it cannot be
  added without breaking the baseline. Dropped.
- **DEMO-002 开发井总数 36-vs-38 cross-location conflict.** The documented 38 lives in
  prose extracted under non-canonical names (`本方案规划开发井总数`, `实施计划钻井总数`)
  with no scope, so honest `CONSISTENCY-001` sees a single complete-key value (36) and
  PASSes. Catching this honestly requires better extraction/normalization of prose
  parameter mentions, not a rule shortcut. Recorded as an extraction limitation.

## Backup

The external source was backed up before any correction as
`golden_cases_demo.jsonl.orig` (outside git). The repository mirror is the normalized,
honest source used by tests.
## 当前收口后的解释

Golden/DEMO 规则仍是 `DEMO_ONLY` 演示数据，不是 42 章正式规则库。RuleStatus 继续只有 `PASS`、`FAIL`、`UNKNOWN`；证据缺失、章节不确定或不完整 sibling 均保持 fail-closed，不把 UNKNOWN 改成 PASS。LLM 是初审补充来源，ProviderError、配置错误和输入超限不会覆盖规则 Finding；非法 evidence 不保存为 AI Finding。上述行为差异是明确的安全语义，不是降低断言。
