# 后续功能计划（待实施，不在本次范围）

> 本文件记录用户提出的三项增强想法，供后续迭代。均**尚未实现**。每项都标注了已有可复用基础，
> 以便实施时不重复造轮子，并延续项目红线（第一性原理 / fail-closed / 仅脱敏数据 / 安全）。

---

## 想法 1：显示 LLM 审查方案后的反馈信息

**目标**：初审完成后，在页面上明确显示这次「AI 复核」实际发生了什么——用了哪个 provider、是否成功、
若失败则显示原因（而不是静默）。让使用者知道 AI 那一层到底跑没跑、可不可信。

**已有基础**：
- 流水线已捕获失败：`ReviewPipeline.run` 里 `state.llm_review_error`（[app/review/pipeline.py](app/review/pipeline.py) 第 55、120 行），
  在线 LLM 抛 `LLMProviderError` 时记录原因、保留规则结果、不崩。
- `LLMConfig` / `build_provider` 已知道当前用的是 Mock 还是 Anthropic。

**建议做法**：
1. `ReviewSummary`（[app/api/schemas.py](app/api/schemas.py)）增加字段：`llm_provider: str`、`llm_ok: bool`、`llm_detail: str | None`。
   `review_case` 从 `run.llm_review_error` 和当前 config 填充。
2. 前端结果区顶部加一行「AI 复核：用 <provider> · 成功 / 未完成（原因）」。成功时可选显示本次 AI 贡献了几条 finding。
3. 若 `llm_review_error` 非空，用醒目黄条提示「本次 AI 复核未完成，仅规则检查结果可用，请人工补充」。

**注意**：仍 fail-closed——AI 未完成绝不影响规则结果，也绝不暗示「AI 通过=方案正确」。反馈里不得出现 key/base_url/请求体。

**测试**：mock 一个失败的 provider → 断言 summary.llm_ok=false、detail 非空且不含 key；mock 成功 → llm_ok=true。

---

## 想法 2：方案审查历史记录（可留存、可手动删除）

**目标**：每次上传一个方案都留一条历史记录，页面能列出「历史案例」，点进去看当时的初审结果，并可手动删除。

**已有基础**：
- 案例与初审结果**已经持久化**在 SQLite：`save_case` / `get_case` / `get_run`
  （[app/persistence/repository.py](app/persistence/repository.py)），每个案例有唯一 `case_id`。
- 删除流程已实现且安全：`delete_case_to_recycle_bin`（移入回收站）+ 二次确认永久删除
  （API `POST /api/cases/{id}/delete-confirm`、`DELETE /api/cases/{id}`）。
- 案例带 `created_at` 时间戳、`statistics`（含 document_count）。

**缺什么**：
- 一个**列出案例**的 repository 方法 + API：`list_cases(limit, offset) -> [{case_id, file_name, created_at, finding_count, final_status}]`
  （排除回收站里的）。
- 前端一个「历史记录」区/页：表格列出案例，每行「查看」「删除」。「查看」= `GET /api/cases/{id}/findings` 渲染成现在的问题卡片；
  「删除」= 走现有两步删除。

**建议做法**：
1. repository 加 `list_active_cases()`（join 排除回收站，按 created_at 倒序，分页）。
2. `GET /api/cases` 返回列表；`GET /api/cases/{id}`（若无则加）返回单个案例概要 + summary。
3. 前端：顶部加「历史记录」入口，列表 + 查看/删除按钮；删除复用现有确认流程。

**注意**：删除仍两步防误删；列表不含原文内容（只列元数据 + 统计）；不破坏「原文不进 git、匿名包脱敏」等约束。

**测试**：建 3 个案例 → list 返回 3 条按时间倒序；删 1 个 → list 返回 2 条且被删的进回收站；分页边界。

---

## 想法 3：一次最多上传 3 个方案，做版本比对

**目标**：允许一次上传最多 3 份方案（同一方案的不同版本），系统自动配对并给出**参数级差异**
（哪个参数从多少改成了多少、有没有说明原因），做版本比对。

**已有基础（大部分已经写好，只差接线！）**：
- **文件配对**：`app/diff/pairing.py` 的 `pair_documents(files)`、`assess_pair(...)`、`PairingTier`、
  `PairingConfirmationRequired`——已能按文件名/版本号配对，低置信度要求人工确认。
- **参数差异**：`app/diff/parameter_diff.py` 的 `diff_parameters(...)` → `ParameterDifference`
  （ADDED/REMOVED/CHANGED/UNCHANGED/UNKNOWN_SCOPE），scope 不完整输出 UNKNOWN 不误报。
- **多文档流水线**：`ReviewPipeline.run` 已接受 `documents: list[ParsedDocument]`，VERSION-001/002 已在跨版本场景工作
  （DEMO-003 双版本 golden 就是证明）。
- 上传接口 `POST /api/cases` 目前一次一个文件，`CaseRecord.files` 是 `list`，本就支持多文件存储。

**缺什么**：
- 上传接口/前端支持**一次选 2–3 个 DOCX**（加数量上限校验：≤3）。
- `review_case` 在多文档时，除跑各自规则外，调用 `pair_documents` + `diff_parameters` 产出一个**差异视图**，
  作为结果的一部分返回（新 schema：`ParameterDiffResponse`）。
- 前端一个「版本差异」区：表格列出每个参数的 旧值 → 新值、是否有变更说明、UNKNOWN_SCOPE 的标注。
- 低置信度配对时，前端弹「请确认这两份是同一方案的不同版本」，对应 `PairingConfirmationRequired`。

**建议做法**：
1. `POST /api/cases` 接受多文件（`list[UploadFile]`），校验 1–3 个、都是 DOCX、总大小限制。
2. `review_case`：`len(documents) >= 2` 时跑配对 + 参数差异，写入 run（可加 `run.parameter_diffs`，
   持久化仿照 `evidence_locations` 的加列 + 自动迁移）。
3. 新端点或扩展 summary 返回差异；前端渲染「版本差异」表 + 配对确认交互。
4. 导出（Excel/Word）增加「版本差异」工作表/章节。

**注意**：差异比对必须走**完整比较键**（canonical_name+subject+time_scope+statistical_scope+condition），
任一维度缺就 UNKNOWN_SCOPE，不臆测（`diff_parameters` 已实现这一点，别绕过）。仍仅文本型 DOCX。

**测试**：DEMO-003 V1+V2 → 配对成功、`diff_parameters` 抓到建设周期 24→18 等变化；上传 4 个 → 422；
scope 不同的参数 → UNKNOWN_SCOPE 不误报。

---

## 实施优先级建议

1. **想法 1（LLM 反馈）** 最小、纯增量，建议先做。
2. **想法 2（历史记录）** 中等，持久化已就位，主要是 list API + 前端列表。
3. **想法 3（版本比对）** 价值最高但工作量最大——不过 `app/diff/` 已把最难的配对与差异算法写好并测过，
   主要是多文件上传 + 接线 + 前端差异视图。

三项都不改动规则引擎的第一性原理与 fail-closed 语义，也不放松「仅脱敏数据 / 密钥只进 keyring」的安全约束。
