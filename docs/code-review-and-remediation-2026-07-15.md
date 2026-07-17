# Code Review 记录与修复方案（合一）· 2026-07-15

> **一份文件搞定：** 上半部分是 **Review 问题与证据**，下半部分是 **按 Task 的修改建议（TDD）**。组员只读本文件即可审查结论 + 动手修复。
>
> **审查性质：** 只读；未改业务代码。  
> **代码根：** `review/.claude/worktrees/kernel-implementation`（或已同步的 `review/main` `app/`）。  
> **测试基线：** `python -m pytest -q` → 337 passed, 8 skipped（无 `REVIEW_DEMO_ROOT`）。  
> **红线：** 第一性原理 + 对抗性测试 + fail-closed（详见下文 Global Constraints）。

**目录**

1. [Part A · Review 记录](#part-a--review-记录)
2. [Part B · 修复实施计划](#part-b--修复实施计划-tdd)

---

# Part A · Review 记录


| 项 | 内容 |
|----|------|
| **日期** | 2026-07-15 |
| **审查对象** | `review/.claude/worktrees/kernel-implementation`（`worktree-kernel-implementation` @ `1f8c47c`）；对照 `review/main` @ `1d17313`（main 在 worktree 之上多 4 个 docs/chore 提交，`app/` 内核已在 main） |
| **审查性质** | **只读**；未改业务代码 |
| **审查人/方式** | Claude Code 多角度并行审查（安全 / 规则引擎 / API·管线·LLM / 架构·测试）+ 关键路径人工复核 |
| **方法依据** | 第一性原理（Finding = 事实 + 证据 + 通用规则）；对抗性审查（正/反向断言、禁假绿）；项目红线见 `CLAUDE.md` |
| **测试基线** | `python -m pytest -q` → **337 passed, 8 skipped**（未设 `REVIEW_DEMO_ROOT`） |
| **修复方案** | **本文 Part B**（同一文件下半：TDD Task 1–15） |

> **给修复同学：** 每条问题都含 **位置 / 现象 / 证据 / 失败场景 / 建议改法 / 对应 Task**。先写失败测试再改实现。

---

## 1. 总体结论

| 维度 | 结论 |
|------|------|
| 本地 Demo（Mock + 规则库） | **可用 / 可演示** |
| 本地正式启用在线 LLM | **有条件**；须先修 fail-closed 对称、AI 失败可见、密钥/URL 门控 |
| 公网 / 多用户 | **不在目标内，不具备** |
| 第一性原理 / 反作弊 | **大体良好**；未见 `legacy_compatibility` 复活；**TERM-001 / EVIDENCE-001 存在假绿** |
| 合并就绪（Mock 路径） | **是** |
| 合并就绪（在线 LLM 当生产） | **否**（见 Critical） |

---

## 2. 优点（保留，避免误删）

1. **分层清晰：** `parsers → extraction → rules → review → llm/persistence/reports/api`。  
2. **三值 + 证据门禁：** `apply_evidence_gate` 不把 UNKNOWN 洗成 PASS。  
3. **反作弊守门：** `tests/unit/test_compatibility_safety.py`；app 内无 `legacy_compatibility`。  
4. **密钥分离：** keyring（WinVault 类型校验）；GET 只回 `key_present`。  
5. **路径/上传：** `safe_join`、UUID4 案例目录、100MB 流式、DOCX zip 结构检查。  
6. **匿名导出：** 字段白名单，无正文/位置/厂商元数据。  
7. **前端 XSS：** finding 字段 `escapeHtml`。  
8. **诚实文化：** `golden-status-deviation.md`、无 DEMO 时 golden 诚实 skip。  
9. **本地绑定习惯：** `scripts/run_local.py` → `127.0.0.1:8765`。  
10. **LLM adapter：** SDK-free httpx；`follow_redirects=False`；伪造 span id → `LLMProviderError`。

---

## 3. 问题清单（含证据）

严重度：

- **Critical：** 假绿 / 密钥外泄 / 用户把失败当成功  
- **Important：** 正确性缺口、安全边界、可见性  
- **Minor：** 体验与文档  

---

### C1. LLM 校验失败会拖垮整条管线，规则 finding 对用户消失

| 字段 | 内容 |
|------|------|
| **严重度** | Critical |
| **位置** | `app/review/pipeline.py` → `llm_reviewed`（约 106–144 行）；`app/pipeline.py` → `StageRunner`；`tests/unit/test_review_pipeline_failure.py` |
| **现象** | `LLMProviderError` 被吞掉并设置 `llm_review_error`，规则可 reconcile；但空 `evidence_span_ids` / 二次 `validate_findings` 抛 `ValueError` 时，StageRunner 在 `LLM_REVIEWED` 停表，**不跑 `reconciled`**，`findings=[]` |
| **证据** | ① `pipeline.py` 中 `except LLMProviderError` 与随后 `validate_findings` 再 `raise ValueError` 不对称；② 单测 `test_empty_llm_evidence_fails_and_never_becomes_ready` **刻意期望** `final_status == "FAILED"` 且不进入 `READY_FOR_HUMAN_REVIEW`；③ `test_invalid_llm_evidence_fails_and_stops_before_reconciliation` 断言 `run.findings == []` 且 `rule_results` 已有数据 |
| **失败场景** | 在线模型返回 `"evidence_span_ids": []` 或未知 span id（非 adapter 包装路径）→ 规则已检出 FAIL，API 侧 findings 为空 |
| **根因** | 把「AI 输出不合格」等同于「整案审查失败」；与红线「外部 LLM 失败不得抹掉确定性规则结果」冲突 |
| **建议改法** | `validate_findings` 拒绝空证据；`llm_reviewed` 将 `LLMProviderError|TypeError|ValueError` 统一记入 `llm_review_error` 后 return；始终执行 reconcile。**改写**上述单测期望 |
| **修复 Task** | Remediation Task 1 |

---

### C2. `final_status=FAILED` 在 API/UI 上像「没问题」

| 字段 | 内容 |
|------|------|
| **严重度** | Critical |
| **位置** | `app/api/routes.py` → `review_case`（约 322–335 行）；`web/app.js` → `renderResult`（约 224–245 行） |
| **现象** | 审查始终返回 201 + `ReviewSummary`；前端不检查 `final_status`；`findings.length===0` 时文案为「本次未发现规则可判定的问题」 |
| **证据** | ① `ReviewSummary` 含 `final_status` 但 UI 未读；② 空 findings 分支固定成功文案（`app.js`）；③ 与 C1 组合：FAILED + 空列表 → 专家以为方案干净 |
| **失败场景** | C1 触发后，用户导出/签字风险 |
| **建议改法** | FAILED 时 422 或明确 error 字段；UI 非 `READY_FOR_HUMAN_REVIEW` 显示失败横幅；禁止用空成功文案掩盖 FAILED |
| **修复 Task** | Remediation Task 2 |

---

### C3. 无鉴权改 `base_url` + 保留 keyring 密钥 → 密钥外泄

| 字段 | 内容 |
|------|------|
| **严重度** | Critical（本机多进程威胁模型） |
| **位置** | `app/llm/config_store.py` → `save`（约 50–69 行）；`app/api/routes.py` → `POST /api/llm/config`、`POST /api/llm/health`；`app/llm/adapters/anthropic.py` 发请求带头 |
| **现象** | `api_key` 为 null/省略时**不更新密钥**，但可改写 `base_url`；下次 health/review 用新 URL 带 `x-api-key` 发出 |
| **证据** | ```python<br># config_store.save 关键：<br>if api_key:<br>    self._credentials.set_key(...)<br># 无 api_key 时仍写入新 base_url 到 JSON<br>``` 且无「base_url 变更必须重输 key」分支 |
| **失败场景** | 本机任意进程 `POST /api/llm/config` 把 base_url 改为攻击者主机 → `POST /api/llm/health` 触发密钥出站 |
| **建议改法** | key 已存在且 base_url/provider 变化时强制非空 `api_key`；或 host 白名单 + 本地 token |
| **修复 Task** | Remediation Task 9 |

---

### C4. 放宽版 LLM URL 策略 = 带密钥的内网可达（SSRF 面）

| 字段 | 内容 |
|------|------|
| **严重度** | Critical（与 C3 叠加） |
| **位置** | `app/security/url_policy.py` → `validate_llm_base_url`（约 120–146 行）；adapter 使用该函数而非 `validate_base_url` |
| **现象** | 允许 `http`、私网、loopback、非 443；严格 `validate_base_url`（仅公网 HTTPS）未用于 LLM 路径；adapter `follow_redirects=False`（好），但无 DNS 再校验 |
| **证据** | `validate_llm_base_url` 仅检查 scheme∈{http,https}、无 userinfo/fragment；**不**调用 `_is_public_ip_literal` |
| **失败场景** | `http://169.254.169.254` 或内网管理口 + key |
| **建议改法** | 默认严格；「允许内网网关」显式开关；确认后的 host 白名单 |
| **修复 Task** | Remediation Task 9 |

---

### R1. TERM-001（`alias_normalization`）在生产管线几乎不可能 FAIL（假绿）

| 字段 | 内容 |
|------|------|
| **严重度** | Critical（第一性原理 / 假绿） |
| **位置** | `app/review/pipeline.py`：先 `normalize_facts` 再 `RuleEngine.evaluate`；`app/rules/operators.py` → `alias_normalization`（约 412–428 行） |
| **现象** | 术语归一后 `canonical_name` 已是规范名；operator 见「有 canonical 事实」即 **PASS**。依赖「仅有 alias、尚无 canonical」的 FAIL 分支在生产路径上**不可达** |
| **证据** | ```python<br>canonical_facts = _named_facts(context, canonical_name)<br>if canonical_facts:<br>    return _outcome(PASS, "术语已归一", ...)<br>alias_facts = [f for f in context.facts if f.raw_name in aliases]<br>if alias_facts:<br>    return _outcome(FAIL, ...)<br>``` 与 pipeline 中 `normalize_facts(facts, terminology)` **先于** `rule_checked` |
| **失败场景** | 正文只写别名「钻井总数」→ 抽取后 `canonical=开发井总数, raw=钻井总数` → TERM-001 PASS，不报未归一 |
| **建议改法** | 以 `raw_name in aliases` 判 FAIL；PASS 仅当表面已用规范名（见修复计划代码） |
| **修复 Task** | Remediation Task 4 |

---

### R2. TERM-001 只注入术语表第一条

| 字段 | 内容 |
|------|------|
| **严重度** | Critical |
| **位置** | `app/rules/ruleset.py` → `load_production_rules`（约 164–168 行） |
| **现象** | `next(iter(terminology.canonical_to_aliases.items()))` 只取第一项；TERM-002 却注入**全部** terms |
| **证据** | 同上行号；对比 `load_repo_rules` 中对 `prose_alias_unnormalized` 的全量 `terms` 列表注入 |
| **失败场景** | 术语表多 canonical 时，除第一条外别名永远不被 TERM-001 检查 |
| **建议改法** | 按 canonical fan-out 多条 `RuleDefinition`，或 `terms: [...]` 统一遍历 |
| **修复 Task** | Remediation Task 5 |

---

### R3. EVIDENCE-001（`evidence_required`）在真实 DOCX 上几乎恒 PASS

| 字段 | 内容 |
|------|------|
| **严重度** | Critical |
| **位置** | `app/rules/operators.py` → `evidence_required`（约 431–448 行）；`app/rules/engine.py` 传入全文 spans；`selectors` 未在 engine 应用 |
| **现象** | `len(context.spans) >= min_evidence` → 任意非空方案段落很多 → 恒 PASS |
| **证据** | ```python<br>return _outcome(<br>    PASS if len(context.spans) >= required else FAIL, ...)<br>``` |
| **失败场景** | 事实 span 断链或证据空洞，只要文档够长仍「证据充分」 |
| **建议改法** | 检查相关 fact 的 `source_span_id` 是否存在于 spans，且 distinct 数量 ≥ min；**禁止**用全文 span 数当充分条件 |
| **修复 Task** | Remediation Task 6 |

---

### I1. `llm_review_error` 未出 API/DB/UI（AI 静默跳过）

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/review/pipeline.py` 字段 `llm_review_error`；`app/api/schemas.py` → `ReviewSummary` **无此字段**；`routes.review_case` 未映射；ORM `ReviewRunORM` 无列 |
| **证据** | 全仓 `llm_review_error` 仅出现在 pipeline 与设计文档；schemas 的 ReviewSummary 字段列表止于 `rule_count` |
| **失败场景** | 网络/401 后用户以为 AI 二审已跑 |
| **建议改法** | Summary 增加 `llm_completed` / `llm_review_error`；UI 黄条；可选 ORM 列 |
| **修复 Task** | Remediation Task 2–3 |

---

### I2. 持久化正文启发式可 422 丢掉整次审查结果

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/persistence/repository.py` → `_looks_like_full_body` / `_safe_text`（约 575–660 行）；`_finding_row` 用 `_safe_text` 校验 description/suggestion |
| **证据** | ```python<br>def _looks_like_full_body(value: str) -> bool:<br>    return value.count("\n") >= 3 or len(value) > 1_000 or (...)<br>``` 且 `_contains_prohibited_content` 调用它 |
| **失败场景** | LLM 返回多段/超 1000 字说明 → `save_run` ValueError → API 422「审查结果无法持久化」→ 客户端一无所有 |
| **建议改法** | finding 正文保留 4000 与密钥扫描，去掉换行/1000 启发式；无效 LLM 行单独丢弃，勿拖垮规则行 |
| **修复 Task** | Remediation Task 11 |

---

### I3. `max_pages=300` 只配置、不执行

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/settings.py` `max_pages=300`；`/api/config` 下发；`app/parsers/docx_parser.py` **无引用** |
| **证据** | `rg max_pages app/` 仅 settings + routes 配置回显，parser/case_files 无强制 |
| **失败场景** | 近 100MB 超长文本 DOCX 全量解析并可能全量送 LLM → 内存/超时/意外出网体量 |
| **建议改法** | 解析后按段落/span 预算 fail-closed 422；手册写明「页≈段」近似 |
| **修复 Task** | Remediation Task 10 |

---

### I4. DOCX zip 解压放大未设上限

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/storage/case_files.py` → `validate_docx_package`（约 39–49 行） |
| **证据** | 仅 `is_zipfile` + 两个成员名；不读 `ZipInfo.file_size` |
| **失败场景** | 小压缩包 → 巨大 `word/document.xml` → OOM |
| **建议改法** | 未压缩单文件/总和上限（如 3× max_file_bytes） |
| **修复 Task** | Remediation Task 10 |

---

### I5. 每请求新建 Engine/Session 且不关闭

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/persistence/db.py` → `create_session`；`app/api/routes.py` → `_repository()` |
| **证据** | 每次 `create_engine(...)` + `Session(engine)`，无全局单例、无 `close/dispose` |
| **失败场景** | 长演示会话句柄/SQLite 锁压力 |
| **建议改法** | engine 单例 + 请求结束 close |
| **修复 Task** | Remediation Task 12 |

---

### I6. 重新审查清空专家状态

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/persistence/repository.py` → `save_run`（约 165–186 行）删除全部 Finding 再插入 |
| **证据** | `query(FindingORM).delete` 后按新 pipeline 输出插入，默认 `pending` |
| **失败场景** | 专家 confirmed 后用户再点审查 → 状态丢失 |
| **建议改法** | 按 `finding_id` 合并恢复 status/note |
| **修复 Task** | Remediation Task 13 |

---

### I7. `POST /api/ruleset/reload` 接受任意文件系统 `root`

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/api/routes.py` `reload_ruleset`；`load_active_ruleset(root)`；`RulesetReloadRequest.root` |
| **证据** | `root = Path(request.root) if request.root else None` 后直接加载；成功响应含绝对 `root` 字符串 |
| **失败场景** | 本机投放假 ruleset → 审查结果被操控 |
| **建议改法** | 生产忽略 client root；不回显绝对路径 |
| **修复 Task** | Remediation Task 12 |

---

### I8. Loopback 仅约定、应用层不强制

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `scripts/run_local.py` 硬编码 127.0.0.1；`app/main.py` `__main__` 用 settings；可被 `uvicorn --host 0.0.0.0` 绕过 |
| **证据** | 无启动 assert；`tests/security/test_api_local_only.py` 多断言 Settings 默认 host |
| **建议改法** | 启动 assert host ∈ {127.0.0.1, ::1} |
| **修复 Task** | Remediation Task 12 |

---

### I9. 文档红线与实现矛盾

| 字段 | 内容 |
|------|------|
| **严重度** | Important（协作误导） |
| **位置** | worktree `CLAUDE.md`：写「在线 LLM 延后」「代码不在 main」；main `CLAUDE.md`：代码在 main，但仍写 LLM 延后；`app/llm/provider.py` docstring 仍写 adapters deferred |
| **证据** | 磁盘存在 `app/llm/adapters/anthropic.py`、`factory.py`、`config_store.py` 与 UI 配置面板 |
| **建议改法** | 红线改为「在线 LLM 已实现、默认 Mock、opt-in」；统一 git 位置表述 |
| **修复 Task** | Remediation Task 14 |

---

### I10. `app/diff` 未接入产品路径

| 字段 | 内容 |
|------|------|
| **严重度** | Important（架构/预期管理） |
| **位置** | `app/diff/pairing.py`、`parameter_diff.py`；仅 tests/docs 引用 |
| **证据** | `rg "from app.diff\|import.*pairing\|parameter_diff" app/` 无生产引用 |
| **建议改法** | 接线多版本审查或标 deferred 并从结构图移出 |
| **修复 Task** | Remediation Task 14 |

---

### R4. 跨参数 op 丢弃 incomplete sibling → 可能 fail-open PASS

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `app/rules/operators.py` → `_one_complete_fact_per_operand`（约 165–171 行） |
| **证据** | 每组只取 `_usable`；incomplete 直接丢掉；`all_equal` 则 incomplete → UNKNOWN |
| **失败场景** | 乙=6 完整 + 乙=9 缺 scope → 用 6 做 sum PASS |
| **建议改法** | 存在同名 incomplete 或冲突 → UNKNOWN |
| **修复 Task** | Remediation Task 7 |

---

### R5. 跨参数比较不检查单位

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `sum_equals` / `product_approximately_equals` / `less_or_equal` 无 `canonical_unit` 检查 |
| **证据** | `rg canonical_unit app/rules/operators.py` 无匹配（审查时） |
| **失败场景** | 产量 vs 井数等不同量纲数值比较 PASS/FAIL |
| **建议改法** | 单位集合 size>1 或混 None/非 None → UNKNOWN |
| **修复 Task** | Remediation Task 7 |

---

### R6. `sum_equals` 使用精确浮点 `==`

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `operators.py` 约 238–239 行 `total == target_fact.normalized_value` |
| **证据** | 同文件 `product_approximately_equals` 有 `relative_tolerance`，sum 无 |
| **失败场景** | 大单位换算后浮点噪声 → 假 FAIL |
| **建议改法** | `math.isclose` 或共享容差 helper |
| **修复 Task** | Remediation Task 7 |

---

### R7. `required_sections_exist` 子串匹配可 false-PASS

| 字段 | 内容 |
|------|------|
| **严重度** | Important |
| **位置** | `operators.py` 约 181–186 行 `section == part or section in part` |
| **证据** | 代码字面；`"3.1" in "3.10 ..."` 为 True |
| **失败场景** | 缺 3.1 但有 3.10 → 完整性 PASS |
| **建议改法** | 默认精确相等 |
| **修复 Task** | Remediation Task 8 |

---

### Minor 汇总

| ID | 问题 | 位置 | 建议 | Task |
|----|------|------|------|------|
| M1 | `LLMRequest.model="mock"` 写死 | pipeline.py | 传配置 model | 1 顺带 |
| M2 | UI 把 hybrid 标成「规则」 | app.js | 分支「规则+AI」 | 2 |
| M3 | 全量 span 拼进 LLM 无预算 | pipeline.py | 截断 + 声明 truncated | 10 相关 |
| M4 | 启用 anthropic 无外发强提示 | web | 警告条 | 9/14 |
| M5 | Golden 软匹配 | test_demo_golden.py | 收紧断言 | 15 |
| M6 | `issue_response_status_exists` 要求 status_terms 未用 | operators.py | 删死参数或实现 | 可选 |
| M7 | 仅表头无数据行 completeness PASS | operators.py | 0 数据行 → UNKNOWN | 可选 |
| M8 | engine 不校验 parameters 元素类型 | engine.py | 非 str 拒绝 | 可选 |

---

## 4. 安全专项对照表

| 控制项 | 状态 | 备注 |
|--------|------|------|
| Keyring 存 key | ✅ | WinVault 类型校验 |
| 日志/匿名包去密钥 | ✅ | |
| 路径穿越 / 扩展名 | ✅ | |
| 100MB | ✅ | |
| 300 页 | ❌ | 未执行 |
| content-as-data | ✅ | 有测试 |
| 无 eval/exec | ✅ | app 业务路径 |
| 无鉴权 + 放宽 URL | ❌ | C3/C4 |
| 绑定 127.0.0.1 | ⚠️ | 约定正确，不强制 |

---

## 5. 规则引擎专项对照表

| 检查项 | 结果 |
|--------|------|
| prose-grep 作弊 / rule_id 硬编码 | 未发现 |
| 三值 + evidence gate 基础 | 成立 |
| TERM-001 生产路径 | **假绿**（R1/R2） |
| EVIDENCE-001 | **齿软**（R3） |
| 跨参数 scope 不强制共享 | 符合 CLAUDE 意图 |
| 跨参数 incomplete/单位 | **缺口**（R4/R5） |

---

## 6. 测试与文档漂移

| 项 | 说明 |
|----|------|
| 假绿单测固化 C1 | `test_review_pipeline_failure.py` 期望整管线 FAILED — 修复时必须改测 |
| 无 `llm_review_error` 契约测 | 可静默回归 |
| CLAUDE 与实现 | 在线 LLM / 代码位置表述过时 |
| golden | 修 TERM/EVIDENCE 后可能变化 — 诚实更新 deviation 文档 |

---

## 7. 修复优先级（执行顺序）

```
P0: C1 + C2 + R1 + R2 + R3     → 假绿与用户诚实
P0: C3 + C4                     → 密钥/URL（启用真实 key 前）
P1: I1 + I2 + R4–R7 + I3 + I4
P2: I5–I10 + Minors + 文档
P3: Task 15 总验收
```

详细步骤与代码样例 → **见本文 Part B（下方 TDD Task）**。

---

## 8. 审查元数据

| 项 | 值 |
|----|----|
| 是否改代码 | 否 |
| 是否跑全量 DEMO golden | 本记录以 337p/8s 为准；带 DEMO_ROOT 以执行机为准 |
| 是否真实外网 LLM | 否（adapter 单测 mock httpx） |
| 后续 | 组员按 Remediation Plan TDD；修完后更新本记录「状态」列为 Fixed |

---

## 9. 问题索引（快速查）

| ID | 一句话 | Task |
|----|--------|------|
| C1 | LLM 校验失败抹掉规则 finding | 1 |
| C2 | FAILED 在 UI 像没问题 | 2 |
| C3 | 改 base_url 可带走旧 key | 9 |
| C4 | LLM URL 过松 + 密钥 | 9 |
| R1 | TERM-001 假绿 | 4 |
| R2 | TERM-001 只第一条术语 | 5 |
| R3 | EVIDENCE-001 恒 PASS | 6 |
| I1 | AI 跳过不可见 | 2–3 |
| I2 | 正文启发式 422 | 11 |
| I3 | 300 页未执行 | 10 |
| I4 | zip bomb | 10 |
| I5 | session 泄漏 | 12 |
| I6 | 重审丢专家状态 | 13 |
| I7 | 任意 ruleset root | 12 |
| I8 | host 不强制 | 12 |
| I9 | 文档漂移 | 14 |
| I10 | diff 未接线 | 14 |
| R4–R7 | 跨参数/章节 | 7–8 |

---

**记录结束。** 修复时请在 PR 描述引用本文件 ID（如 `Fixes: C1, R1`）。

---

# Part B · 修复实施计划（TDD）

> 以下内容由原 `docs/superpowers/plans/2026-07-15-post-review-remediation.md` 并入。  
> **For agentic workers:** 可用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 按 Task 执行。


> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Audience:** 组员拿到本方案即可按 Task 顺序 TDD 修复；无需重读 code-review 长文。
>
> **Origin:** 2026-07-15 只读 code review（安全 / 规则引擎 / API·管线·LLM / 架构·测试）。  
> **Code root:** `review/.claude/worktrees/kernel-implementation`（分支 `worktree-kernel-implementation`）；`review/main` 已含同内核，以工作区实际路径为准。

**Goal:** 消除 review 发现的假绿规则、LLM fail-closed 不对称、在线 LLM 密钥/URL 风险与用户可见诚实性缺口，使本地 Demo 与可选在线 LLM 路径均符合第一性原理与对抗性测试红线。

**Architecture:** 修复按四条工作流推进，互不破坏对方契约：  
(A) **LLM 诚实 fail-closed** — 任何 LLM 失败只丢弃 AI 贡献，永不抹掉规则 finding；错误对 API/UI 可见；  
(B) **规则引擎第一性原理** — TERM/EVIDENCE 按原文事实判定，禁“先归一再判已归一”的假绿；跨参数比较 fail-closed；  
(C) **安全边界** — 改 base_url 需重认证、URL 默认严格、页数/zip 上限、loopback 强制；  
(D) **持久化与文档** — 正文启发式放宽、会话生命周期、CLAUDE/手册对齐实现。

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, pytest, httpx, pydantic v2, python-docx, keyring. 无新增依赖除非 Task 明确写出。

## Global Constraints（永久；每个 Task 隐式包含）

1. **第一性原理：** Finding = 真实事实 + SourceSpan + 通用 operator → 三值结果。禁止 grep 文档结论句；禁止 rule_id / 参数名硬编码特判。
2. **对抗性测试：** 每个改动同时写 **正向**（真问题能检出）与 **反向**（不误报）断言。禁止为通过而降低期望或改 golden 迁就实现；引擎做不到就修引擎或诚实缩小 golden 并记 `docs/golden-status-deviation.md`。
3. **Fail-closed：** UNKNOWN / BLOCK / 缺证据 / 解析失败 / LLM 失败 **不得静默变 PASS**；LLM 失败 **不得抹掉规则 finding**。
4. **不实现：** 自动审批、正式结论签发、自动改源文件、无依据优劣评价、「没发现问题」=「方案正确」。
5. **安全：** key 只进 keyring；禁 eval/exec；文档内容当数据；服务目标绑 `127.0.0.1`。
6. **TDD：** 先写失败测试 → 跑红 → 最小实现 → 跑绿 → 提交。
7. **Python：** `>=3.12,<3.13`。命令在 repo 根（含 worktree 根）执行：`python -m pytest -q …`。
8. **工作流纪律：** 一个 Task 一次逻辑提交；不混无关重构；不碰 PDF/OCR/向量 RAG。

## Review → Task 映射

| Review ID | 摘要 | Task |
|-----------|------|------|
| C1/C2 + R8 | LLM 校验失败整管线 FAILED + UI 像没问题 | Task 1–2 |
| I1 | `llm_review_error` 不可见 | Task 2–3 |
| R1/R2 | TERM-001 假绿 + 只注入第一条术语 | Task 4–5 |
| R3 | EVIDENCE-001 恒 PASS | Task 6 |
| R4/R5/R6 | 跨参数丢弃 incomplete / 无单位 / sum 精确 == | Task 7 |
| R7 | 章节子串误匹配 | Task 8 |
| C3/C4 | base_url 枢轴泄钥 + 放宽 URL | Task 9 |
| I3/I4 | 300 页未强制 + zip bomb | Task 10 |
| I2 | 持久化 body 启发式误杀 | Task 11 |
| I5/I7/I8 | session 泄漏 / ruleset root / host | Task 12 |
| I6 | 重审清空专家状态 | Task 13 |
| I9/I10 | 文档漂移 / orphan diff | Task 14 |
| 验收 | 全量测试 + 诚实报告 | Task 15 |

## File map（将改动的责任边界）

| 文件 | 责任 |
|------|------|
| `app/llm/provider.py` | `validate_findings` 拒绝空证据；清理 deferred stub 文案 |
| `app/review/pipeline.py` | LLM 任意失败 → 保留规则 + reconcile + `llm_review_error` |
| `app/api/schemas.py` / `routes.py` | ReviewSummary 暴露 LLM 状态；FAILED 不装成功 |
| `web/app.js` / `index.html` | 失败/AI 跳过横幅；hybrid 标签 |
| `app/rules/operators.py` | alias / evidence / 跨参数 / 章节匹配 |
| `app/rules/ruleset.py` | TERM 全量术语注入 |
| `app/security/url_policy.py` / `credentials` 调用链 | URL 策略 + base_url 变更门控 |
| `app/llm/config_store.py` | 改 endpoint 需重输 key |
| `app/parsers/docx_parser.py` / `case_files.py` / `settings.py` | 页数与 zip 上限 |
| `app/persistence/*` | 正文启发式；session 生命周期；llm 错误字段；专家状态合并 |
| `CLAUDE.md` / `docs/*` | 红线与实现对齐 |
| `tests/unit/*` `tests/security/*` `tests/contract/*` | 对抗性回归 |

---

## Workstream A — LLM 诚实 fail-closed

### Task 1: validate_findings 拒绝空证据 + LLM 失败不中断 reconcile

**Files:**
- Modify: `app/llm/provider.py`（`validate_findings`）
- Modify: `app/review/pipeline.py`（`llm_reviewed`）
- Modify: `tests/unit/test_review_pipeline_failure.py`
- Modify: `tests/unit/test_llm_provider.py`（若有空证据用例）

**Interfaces:**
- Consumes: `LLMProvider.review` → `LLMResponse`; `validate_findings(findings, allowed_ids) -> list[dict]`
- Produces: `ReviewRun.llm_review_error: str | None` 在校验失败时也设置；`final_status` 在仅 LLM 失败时为 `READY_FOR_HUMAN_REVIEW`；`findings` 至少含规则结果

**Why (第一性原理):** 规则 finding 来自确定性事实与 operator，与 LLM 无关。LLM 输出不合格 = AI 贡献无效，不是整案审查无效。

- [ ] **Step 1: 写失败测试（替换旧“整管线 FAILED”期望）**

在 `tests/unit/test_review_pipeline_failure.py` **替换** `test_empty_llm_evidence_fails_and_never_becomes_ready` 与 `test_invalid_llm_evidence_fails_and_stops_before_reconciliation`：

```python
def test_empty_llm_evidence_skips_ai_keeps_rule_findings() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()], InvalidEvidenceProvider([finding([])])
    )
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_review_error is not None
    assert any(f.origin.value == "rule" for f in run.findings) or any(
        r.rule_id == "R1" for r in run.rule_results
    )
    assert run.findings  # 规则 required_sections 应对缺失章节产生 finding
    assert all(
        f.origin.value != "llm" or f.evidence_span_ids
        for f in run.findings
    )


def test_unknown_llm_evidence_skips_ai_keeps_rule_findings() -> None:
    run = ReviewPipeline().run(
        "case-1", [document()], [rule()],
        InvalidEvidenceProvider([finding(["not-supplied"])]),
    )
    assert run.final_status == "READY_FOR_HUMAN_REVIEW"
    assert run.llm_review_error is not None
    assert run.rule_results
    assert run.findings
```

另在 `tests/unit/test_llm_provider.py` 增加：

```python
def test_validate_findings_rejects_empty_evidence_list():
    with pytest.raises(ValueError, match="evidence"):
        validate_findings(
            [{
                "category": "capacity", "severity": "high", "title": "t",
                "description": "d", "suggestion": "s", "evidence_span_ids": [],
            }],
            ["s1"],
        )
```

- [ ] **Step 2: 跑测试确认失败**

```bash
python -m pytest tests/unit/test_review_pipeline_failure.py tests/unit/test_llm_provider.py -q
```

Expected: 上述新断言 FAIL（旧行为 `final_status==FAILED` 或空 findings）。

- [ ] **Step 3: 实现 `validate_findings` 拒绝空证据**

在 `app/llm/provider.py` 的 `validate_findings` 内，检查 `evidence_ids` 类型之后加入：

```python
        if not evidence_ids:
            raise ValueError("evidence_span_ids must be non-empty")
```

- [ ] **Step 4: 实现 pipeline 统一 LLM 失败路径**

改写 `app/review/pipeline.py` 的 `llm_reviewed`：

```python
        def llm_reviewed() -> None:
            try:
                response = provider.review(
                    LLMRequest(
                        model="review",
                        system_prompt="只输出结构化复核意见",
                        user_content="\n".join(span.text for span in spans),
                        evidence_span_ids=[span.span_id for span in spans],
                    )
                )
                validated = validate_findings(
                    response.findings, [span.span_id for span in spans]
                )
            except (LLMProviderError, TypeError, ValueError) as exc:
                # 任何 LLM 不合格输出/网络错误：跳过 AI，保留规则，诚实记录
                state.llm_review_error = str(exc) if str(exc) else type(exc).__name__
                return
            llm_findings.extend(
                Finding(
                    finding_id=f"llm-{index}",
                    origin=Origin.LLM,
                    category=item["category"],
                    severity=Severity(item["severity"]),
                    parameter=item.get("parameter"),
                    title=item["title"],
                    description=item["description"],
                    suggestion=item["suggestion"],
                    evidence_span_ids=list(item["evidence_span_ids"]),
                    needs_human_review=True,
                )
                for index, item in enumerate(validated)
            )
```

删除原先「二次 validate 再 raise ValueError」导致 StageRunner 停表的路径。  
保留 `reconciled` 始终在成功完成 `llm_reviewed`（含早退）之后执行。

- [ ] **Step 5: 跑测试确认通过**

```bash
python -m pytest tests/unit/test_review_pipeline_failure.py tests/unit/test_review_pipeline.py tests/unit/test_llm_provider.py -q
```

Expected: PASS。

- [ ] **Step 6: Commit**

```bash
git add app/llm/provider.py app/review/pipeline.py tests/unit/test_review_pipeline_failure.py tests/unit/test_llm_provider.py
git commit -m "fix(review): LLM validation failure keeps rule findings fail-closed"
```

---

### Task 2: ReviewSummary / API 暴露 LLM 状态；FAILED 不装成功

**Files:**
- Modify: `app/api/schemas.py`（`ReviewSummary`）
- Modify: `app/api/routes.py`（`review_case`）
- Modify: `web/app.js`（`renderResult`）
- Create or modify: `tests/contract/test_api_llm_status.py`

**Interfaces:**
- Produces: `ReviewSummary.llm_completed: bool`, `ReviewSummary.llm_review_error: str | None`

- [ ] **Step 1: 写契约测试**

```python
# tests/contract/test_api_llm_status.py
def test_review_summary_includes_llm_skip_fields(client, demo_docx_path):
    # 使用强制失败的 provider 注入方式与现有 contract fixture 一致
    # 断言 response.json() 含 llm_completed / llm_review_error 键
    ...
```

实现时：若 contract 难注入坏 provider，至少 **unit 测 schema** + 在 `routes.review_case` 返回字段的纯函数测试。最低要求：

```python
def test_review_summary_schema_has_llm_fields():
    s = ReviewSummary(
        case_id="00000000-0000-4000-8000-000000000001",
        final_status="READY_FOR_HUMAN_REVIEW",
        finding_count=1,
        fact_count=1,
        stages=["UPLOADED"],
        rules_loaded=True,
        rule_count=12,
        llm_completed=False,
        llm_review_error="LLMProviderError",
    )
    assert s.llm_completed is False
    assert s.llm_review_error
```

- [ ] **Step 2: 扩展 schema**

```python
class ReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case_id: str
    final_status: str
    finding_count: int
    fact_count: int
    stages: list[str]
    rules_loaded: bool
    rule_count: int
    llm_completed: bool = True
    llm_review_error: str | None = None
```

- [ ] **Step 3: routes 填充字段**

```python
    return ReviewSummary(
        case_id=run.case_id,
        final_status=run.final_status,
        finding_count=len(run.findings),
        fact_count=len(run.facts),
        stages=[record.stage.value for record in run.stage_records],
        rules_loaded=loaded is not None,
        rule_count=len(rules),
        llm_completed=run.llm_review_error is None,
        llm_review_error=run.llm_review_error,
    )
```

若 `final_status == "FAILED"`（解析/规则硬失败），**仍返回 201 亦可**，但 UI 必须区分；推荐：`final_status == "FAILED"` 时 HTTP 422，body 仍为结构化 JSON（含 stages）。选一种并在测试固定。

- [ ] **Step 4: UI 诚实展示**

在 `web/app.js` `renderResult`：

```javascript
  if (summary.final_status && summary.final_status !== "READY_FOR_HUMAN_REVIEW") {
    parts.push(`<div class="warn">审查未完成（状态：${escapeHtml(summary.final_status)}）。请勿将空结果理解为「方案正确」。</div>`);
  }
  if (summary.llm_completed === false || summary.llm_review_error) {
    parts.push(`<div class="warn">AI 复核未完成：${escapeHtml(summary.llm_review_error || "已跳过")}。下列问题仅含规则引擎结果。</div>`);
  }
  // 空 findings 文案保持：仍强调「不代表方案正确」
```

`hybrid` 来源：

```javascript
  const source =
    finding.origin === "llm" ? "AI 复核" :
    finding.origin === "hybrid" ? "规则+AI" : "规则";
```

- [ ] **Step 5: 测试 + 提交**

```bash
python -m pytest tests/contract tests/unit/test_review_pipeline*.py -q
git add app/api/schemas.py app/api/routes.py web/app.js tests/
git commit -m "feat(api): expose llm skip status and honest UI banners"
```

---

### Task 3: 持久化 `llm_review_error`（可选但推荐）

**Files:**
- Modify: `app/persistence/models.py`（`ReviewRunORM`）
- Modify: `app/persistence/db.py`（`_upgrade_schema`）
- Modify: `app/persistence/repository.py`（`save_run` / hydrate）
- Modify: `app/review/pipeline.py`（已有字段，确保读写）

- [ ] **Step 1: 测试** — 保存 run 带 `llm_review_error`，再 `get_run` 能读回。  
- [ ] **Step 2: 列 `llm_review_error TEXT NULL`** 经 `_upgrade_schema` 增量添加。  
- [ ] **Step 3: save/load 映射；sanitize 用 `_safe_text`（短错误串）。  
- [ ] **Step 4: pytest + commit** `fix(persistence): store llm_review_error on review runs`

---

## Workstream B — 规则引擎第一性原理

### Task 4: TERM-001 按 raw_name 判定（修假绿）

**Files:**
- Modify: `app/rules/operators.py` — `alias_normalization`
- Modify: `tests/unit/test_operators.py`
- 可能：`tests/golden/*` / `docs/golden-status-deviation.md`

**Why:** 生产管线先 `normalize_facts` 再跑规则，旧逻辑「有 canonical 事实 → PASS」把归一**结果**当成文档**原文**已规范。

- [ ] **Step 1: 对抗测试**

```python
def test_alias_normalization_fails_when_raw_name_is_alias_even_if_canonicalized():
    # 模拟 pipeline 归一后：canonical_name 已是规范名，raw_name 仍是别名
    f = fact("1", "开发井总数", 36.0, raw_name="钻井总数", canonical_name="开发井总数")
    out = run("alias_normalization", facts=[f], params={
        "canonical_name": "开发井总数",
        "aliases": ["钻井总数", "总井数"],
    })
    assert out.status is RuleStatus.FAIL


def test_alias_normalization_passes_when_raw_equals_canonical():
    f = fact("1", "开发井总数", 36.0, raw_name="开发井总数", canonical_name="开发井总数")
    out = run("alias_normalization", facts=[f], params={
        "canonical_name": "开发井总数",
        "aliases": ["钻井总数"],
    })
    assert out.status is RuleStatus.PASS


def test_alias_normalization_unknown_when_no_related_facts():
    f = fact("1", "高峰产量", 100.0)
    out = run("alias_normalization", facts=[f], params={
        "canonical_name": "开发井总数",
        "aliases": ["钻井总数"],
    })
    assert out.status is RuleStatus.UNKNOWN
```

- [ ] **Step 2: 跑红** `pytest tests/unit/test_operators.py -k alias_normalization -q`

- [ ] **Step 3: 最小实现**

```python
def alias_normalization(context: OperatorContext, params: dict[str, Any]) -> OperatorOutcome:
    canonical_name = params.get("canonical_name")
    aliases = params.get("aliases", [])
    if (
        not isinstance(canonical_name, str)
        or not canonical_name
        or not isinstance(aliases, list)
        or not all(isinstance(alias, str) and alias for alias in aliases)
    ):
        return _unknown("缺少有效术语归一配置")
    alias_set = set(aliases)
    related = [
        fact
        for fact in context.facts
        if fact.canonical_name == canonical_name or fact.raw_name in alias_set
        or fact.raw_name == canonical_name
    ]
    if not related:
        return _unknown("未找到术语事实")
    # 文档表面仍使用别名 → FAIL（即使 canonical_name 字段已被 pipeline 改写）
    unnormalized = [f for f in related if f.raw_name in alias_set and f.raw_name != canonical_name]
    if unnormalized:
        return _outcome(RuleStatus.FAIL, "术语未归一", unnormalized)
    return _outcome(RuleStatus.PASS, "术语已归一", related)
```

- [ ] **Step 4: 跑绿 + 全 operators 回归**

```bash
python -m pytest tests/unit/test_operators.py tests/unit/test_engine.py -q
```

- [ ] **Step 5: 若 golden 变化** — 诚实更新 `docs/golden-status-deviation.md`，**禁止**为绿而改回假逻辑。  
- [ ] **Step 6: Commit** `fix(rules): TERM-001 judges raw surface form not post-normalize name`

---

### Task 5: TERM-001 全量术语注入

**Files:**
- Modify: `app/rules/ruleset.py` — `load_production_rules` 中 `alias_normalization` 注入
- Modify: `tests/unit/test_ruleset.py` 或 `tests/contract/test_demo_import.py`

- [ ] **Step 1: 测试** — 术语表含 ≥2 个 canonical 时，加载后应产生 **多条** alias_normalization 规则（或单规则多 terms，二选一，推荐 fan-out）：

**推荐设计（与 engine 的 `parameters` fan-out 风格一致）：**  
对每个 `(canonical, aliases)` 生成一条 `RuleDefinition` 副本（`rule_id` 可保持同一 ID 不同 parameter，或 `TERM-001#{canonical}`——**不要**特殊 case rule_id 逻辑进 engine；用 `parameter=canonical` 区分）。

若保持单条 rule_id `TERM-001`：在 loader 中展开为多条 `RuleDefinition`，仅 `params` 与 `parameter` 不同。

```python
def test_alias_normalization_injects_all_terminology_entries(tmp_path, terminology_with_two_terms):
    rules = load_production_rules(rules_yaml, terminology_with_two_terms)
    alias_rules = [r for r in rules if r.operator == "alias_normalization"]
    assert len(alias_rules) >= 2
    names = {r.params["canonical_name"] for r in alias_rules}
    assert names == {"开发井总数", "高峰产量"}  # 示例
```

- [ ] **Step 2: 实现** — 删除 `next(iter(...))`，改为循环 `terminology.canonical_to_aliases.items()` 展开规则行。  
- [ ] **Step 3: pytest + commit** `fix(rules): inject full terminology into TERM-001`

---

### Task 6: EVIDENCE-001 语义重定义（禁「全文非空即充分」）

**Files:**
- Modify: `app/rules/operators.py` — `evidence_required`
- Modify: `tests/unit/test_operators.py` / `test_evidence.py`
- 可能：`app/rules/engine.py`（若采用「检查其他 FAIL 结果的证据」需第二阶段参数——**YAGNI 优先单 operator 自洽方案**）

**第一性原理目标：** 「证据充分」应对 **规则产出的 finding 是否挂有真实 span**，而不是文档段落数量。

**选定方案（组员必须按此实现，避免分叉）：**

`evidence_required` 检查：对当前 context 中 **所有非 PASS 的无法在 operator 内看到**——operator 无其他结果。

因此采用 **自洽可测** 的语义：

> 对每个 `involved` 事实（若 params 指定 `parameter`/`parameters` 则限定），要求其 `source_span_id` 存在于 `context.spans`，且 `min_evidence` 表示「至少 N 个 distinct span」覆盖这些事实。  
> 若无相关事实 → UNKNOWN。  
> **禁止** `len(context.spans) >= min` 作为通过条件。

若 DEMO YAML 的 EVIDENCE-001 原意是「全局最小证据数」：在 `docs/golden-status-deviation.md` 记录语义收紧；实现以第一性原理为准。

- [ ] **Step 1: 对抗测试**

```python
def test_evidence_required_fails_when_fact_span_missing_from_context():
    f = fact("1", "高峰产量", 1.0, span_id="missing")
    spans = [span("text", sid="s1")]
    out = run("evidence_required", facts=[f], spans=spans, params={"min_evidence": 1, "parameter": "高峰产量"})
    assert out.status is RuleStatus.FAIL


def test_evidence_required_passes_with_real_span_links():
    f = fact("1", "高峰产量", 1.0, span_id="s1")
    spans = [span("text", sid="s1")]
    out = run("evidence_required", facts=[f], spans=spans, params={"min_evidence": 1, "parameter": "高峰产量"})
    assert out.status is RuleStatus.PASS


def test_evidence_required_does_not_pass_merely_because_document_has_many_spans():
    f = fact("1", "高峰产量", 1.0, span_id="ghost")
    spans = [span(f"p{i}", sid=f"s{i}") for i in range(50)]
    out = run("evidence_required", facts=[f], spans=spans, params={"min_evidence": 1, "parameter": "高峰产量"})
    assert out.status is not RuleStatus.PASS
```

- [ ] **Step 2–4: 跑红 → 实现 → 跑绿 → commit** `fix(rules): evidence_required checks fact-span linkage not doc size`

---

### Task 7: 跨参数 fail-closed（incomplete sibling + 单位 + sum 容差）

**Files:**
- Modify: `app/rules/operators.py` — `_one_complete_fact_per_operand`, `sum_equals`, `product_approximately_equals`, `less_or_equal`
- Modify: `tests/unit/test_operators.py`

- [ ] **Step 1: 测试**

```python
def test_sum_equals_unknown_when_incomplete_sibling_conflicts():
    # 乙=6 完整 + 乙=9 缺 scope → 不得静默用 6 而 PASS
    facts = [
        fact("t", "总数", 36.0),
        fact("a", "甲", 30.0),
        fact("b1", "乙", 6.0),
        fact("b2", "乙", 9.0, time_scope=None),
    ]
    out = run("sum_equals", facts=facts, params={"target": "总数", "components": ["甲", "乙"]})
    assert out.status is RuleStatus.UNKNOWN


def test_less_or_equal_unknown_on_unit_mismatch():
    left = fact("1", "高峰产量", 100.0)
    left = left.model_copy(update={"canonical_unit": "m3/d"})
    right = fact("2", "处理能力", 200.0)
    right = right.model_copy(update={"canonical_unit": "口"})
    out = run("less_or_equal", facts=[left, right], params={"left": "高峰产量", "right": "处理能力"})
    assert out.status is RuleStatus.UNKNOWN


def test_sum_equals_allows_small_float_noise():
    # 使用会产生二进制误差的小数，期望 PASS（相对或绝对容差）
    ...
```

- [ ] **Step 2: `_one_complete_fact_per_operand`**

对每个 name 的 group：若存在任何 fact 且 **并非全部 usable**，或 usable 值不一致 → `return None, gathered`（UNKNOWN）。  
仅当「全部 usable 且单值」或「仅有 usable 且无 incomplete」时选取（更严：`incomplete` 存在即 UNKNOWN）。

- [ ] **Step 3: 单位** — 多操作数时，收集 `canonical_unit`：若集合 size>1（忽略全 None 的显式策略：全 None 允许；混 None 与非 None → UNKNOWN）。

- [ ] **Step 4: sum** — `math.isclose(total, target, rel_tol=1e-9, abs_tol=1e-6)` 或与 product 共享 helper。

- [ ] **Step 5: pytest + commit** `fix(rules): cross-parameter ops fail closed on scope/unit`

---

### Task 8: required_sections_exist 精确匹配

**Files:** `app/rules/operators.py`, `tests/unit/test_operators.py`

- [ ] **测试：** `"3.1"` 不得匹配 path `"3.10 xxx"`；精确相等或「分段 token」策略二选一，**默认精确相等** `section == part`。  
- [ ] **实现：** 删除 `section in part` 子串分支（或改为边界正则）。  
- [ ] **反向：** 完整标题相等仍 PASS。  
- [ ] **Commit:** `fix(rules): section completeness uses exact path-part match`

---

## Workstream C — 安全

### Task 9: base_url 变更门控 + LLM URL 策略

**Files:**
- Modify: `app/llm/config_store.py`
- Modify: `app/security/url_policy.py`（可选：`allow_private` 开关）
- Modify: `app/api/routes.py` / schemas
- Create: `tests/security/test_llm_config_base_url_gate.py`

**产品决策（固定）：**
1. 当 `key_present` 且新 `base_url` 与已存不同时，**必须**提供非空 `api_key`，否则 422。  
2. 默认 `validate_llm_base_url` 改为：仅 `https` + 非私网 **或** 显式 `allow_private_llm_endpoint=true` 写入 config JSON。  
3. 内网网关：用户勾选「允许私网/HTTP」后才用放宽校验。

- [ ] **Step 1: 安全测试**

```python
def test_changing_base_url_without_key_rejected(tmp_path):
    store = LLMConfigStore(tmp_path / "c.json", FakeCreds(key="sk-test"))
    store.save(provider="anthropic", base_url="https://api.deepseek.com/anthropic", model="m", api_key="sk-test")
    with pytest.raises(ValueError, match="api_key|密钥"):
        store.save(provider="anthropic", base_url="http://evil.example", model="m", api_key=None)
```

```python
def test_default_rejects_private_http_llm_url():
    with pytest.raises(ReviewError):
        validate_llm_base_url("http://127.0.0.1:8080")  # 默认严格后
```

- [ ] **Step 2–4: 实现门控与策略、UI 勾选「允许内网网关」、pytest、commit**  
  `fix(security): require key re-entry on base_url change; tighten LLM URL default`

---

### Task 10: max_pages + zip 解压上限

**Files:**
- `app/settings.py`（已有 `max_pages`）
- `app/parsers/docx_parser.py`
- `app/storage/case_files.py`
- `app/api/routes.py`（解析后检查）
- `tests/security/test_docx_limits.py`

- [ ] **页数：** 解析后若 `paragraph_index` 最大值或 spans 中段落数 > `max_pages` → `ParseError` → API 422。DOCX 无真实页时 **用段落预算** 作为 `max_pages` 的可执行近似，并在 CLAUDE/手册写明。  
- [ ] **Zip：** `validate_docx_package` 检查每个 `ZipInfo.file_size` 与总和 ≤ `max_file_bytes * 3`（或固定 300MB 未压缩上限）。  
- [ ] **对抗测试：** 构造超限元数据（mock ZipInfo）拒绝；正常 DEMO 仍通过。  
- [ ] **Commit:** `fix(security): enforce page budget and zip expansion caps`

---

## Workstream D — 持久化 / 体验 / 文档

### Task 11: 放宽 finding 正文启发式

**Files:** `app/persistence/repository.py`, `tests/unit/test_repository.py` / security tests

- [ ] **`_looks_like_full_body`：** 对 finding description/suggestion **不再**因 `\\n>=3` 或 `len>1000` 拒绝（仍 cap 4000 + 密钥扫描）。  
- [ ] **可保留** 对 `human_note` 的更严策略。  
- [ ] **测试：** 1200 字、含 5 个换行的 description 可 `save_run`。  
- [ ] **Commit:** `fix(persistence): allow multi-paragraph finding prose`

---

### Task 12: Session 生命周期 + ruleset root + host 强制

**Files:** `app/persistence/db.py`, `app/api/routes.py`, `app/main.py` / `scripts/run_local.py`, `app/rules/ruleset.py`

- [ ] **Engine 单例：** `get_engine(db_path)` lru_cache；`create_session` 复用 engine；FastAPI 依赖或 routes 在请求结束 `session.close()`。  
- [ ] **ruleset reload：** 忽略客户端 `root`（或仅 `REVIEW_ALLOW_RULESET_ROOT=1` 测试用）；响应不回显绝对路径（可回显 `loaded/rule_count` only）。  
- [ ] **host：** `main`/`run_local` 启动前 `assert settings.host in {"127.0.0.1", "::1"}`。  
- [ ] **Commit:** `fix(ops): session lifecycle, ruleset root lock, loopback assert`

---

### Task 13: 重审保留专家状态

**Files:** `app/persistence/repository.py`, tests

- [ ] **策略：** `save_run` 删除前读取旧 `finding_id → (review_status, human_note, ai_snapshot)`；新 finding 若 id 相同则恢复 status/note（**不**覆盖新 AI 正文进 `ai_snapshot` 除非首次）。  
- [ ] **测试：** 专家 confirmed 后二次 review 同 id 仍 confirmed。  
- [ ] **Commit:** `fix(review): preserve expert status across re-review`

---

### Task 14: 文档与 CLAUDE 对齐

**Files:** `CLAUDE.md`（main + worktree）、`README.md`、`docs/使用手册.md`、`docs/golden-status-deviation.md`、`app/llm/provider.py` docstring

- [ ] 红线改为：在线 LLM **已实现、默认 Mock、opt-in**；PDF/OCR/RAG 仍延后。  
- [ ] 删除「代码不在 main」过时句（以实际仓库为准）。  
- [ ] 结构图补 `adapters/config_store/factory`。  
- [ ] `app/diff`：在 CLAUDE 标 **deferred / 未接线** 或删除结构暗示。  
- [ ] **Commit:** `docs: align red lines with online LLM and layout`

---

### Task 15: 总验收（对抗性 + 诚实报告）

- [ ] **无 DEMO：** `python -m pytest -q` → 全绿（允许 honest skip）。  
- [ ] **有 DEMO：** `REVIEW_DEMO_ROOT=… python -m pytest -q` → 全绿或偏差已文档化。  
- [ ] **安全子集：** `python -m pytest tests/security -q`  
- [ ] **grep 守门：** 确认无 `legacy_compatibility`、无 `eval(` / `exec(` 于 `app/`。  
- [ ] 按 `docs/test-report-template.md` 填一份 **诚实** 验收记录（未跑项写「未执行」）。  
- [ ] 不宣称「方案正确率 xx%」除非有基线定义。

---

## 执行顺序与并行

```
Task 1 → 2 → 3     (A: LLM 诚实)     可与 B 部分并行，但 1 优先合入
Task 4 → 5 → 6     (B: TERM/EVIDENCE)
Task 7 → 8         (B: 跨参数/章节)
Task 9 → 10        (C: 安全)         建议在启用真实 key 前完成
Task 11 → 12 → 13  (D: 体验)
Task 14 → 15       (文档 + 验收)
```

**人员分工建议：**
- 同学 A：Task 1–3  
- 同学 B：Task 4–8  
- 同学 C：Task 9–10、12  
- 同学 D：Task 11、13–15  

合并前互相 rebase；冲突优先保留 **fail-closed 与对抗测试**。

---

## 明确不做（YAGNI）

- PDF / OCR / 视觉 / 向量 RAG  
- OpenAI SDK 官方适配（非 Anthropic 兼容）  
- 公网多用户鉴权体系（超出本地工作坊；仅做 loopback + 配置门控）  
- 重写整个前端框架  
- 为假绿恢复 `legacy_compatibility`

---

## Self-review checklist（写计划时已核）

- [x] Critical C1–C4、R1–R3 均有 Task  
- [x] Important 跨参数/页数/持久化/文档有 Task  
- [x] 每 Task 含 TDD 与 commit 信息  
- [x] 无「适当处理」占位；关键代码块可直接落地  
- [x] 与第一性原理 / 对抗测试 / fail-closed 红线一致  

---

*计划完。组员从 Task 1 开始；阻塞时先补对抗测试再改实现。*
