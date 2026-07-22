# 开发方案审查助手（本地版）— 内核设计文档

> 日期：2026-07-14
> 范围：第一性原理内核（kernel-first）
> 状态：待用户审阅
> 来源需求：`../../本地版开发提示词_最终版.md`

---

## 1. 本次范围与非范围

### 1.1 本次实现（kernel）

在 `review/` 目录独立构建可运行的本地应用，走通端到端第一性原理内核：

```
案例(多文件) → DOCX解析 → SourceSpan → 参数事实 → 术语归一/单位换算
→ 三值规则引擎 → LLM复核(Mock,证据门禁) → 合并去重 → Finding
→ 专家复核 → Excel/Word/匿名包导出
```

必须真实跑通 DEMO-001/002/003/004 金标准回归，不伪造结果。

### 1.2 延后（留接口，不实现）

本次**只处理文本型 DOCX**。以下模块不写实现代码，仅在 `parsers/` 留抽象契约，**待用户明确提出再做**：

- **PDF 文本层解析（PyMuPDF）— 明确下调，用户提出前不做**
- **扫描页视觉模型 / PaddleOCR 兜底 — 明确下调，用户提出前不做**
- 在线 LLM（Anthropic / OpenAI 真适配器）——本次用 Mock
- 向量 RAG（sentence-transformers）——本次规范/历史意见用关键词检索占位
- Streamlit（改用 FastAPI + 现代静态前端）

> PDF/OCR 优先级最低。文件上传校验遇到非 DOCX 文件时，明确提示「暂不支持，仅处理文本型 DOCX」，不静默跳过，也不调用任何 PDF/OCR 代码。

### 1.3 不得实现（硬约束，永久）

自动审批、正式结论自动签发、自动改源文件、无依据优劣评价、以「没发现问题」表述为「方案正确」。

---

## 2. 关键决策记录

| # | 冲突 / 选择 | 决定 | 理由 |
|---|---|---|---|
| D1 | 整份 spec 是 10 人日系统 | 本次只做 kernel | 单 agent 会话，先立稳第一性原理内核 |
| D2 | spec 写 Streamlit，用户要现代审美 | FastAPI + 静态现代前端 | Streamlit 美化受限，与审美要求冲突 |
| D3 | spec §7.3 只允许 PASS/FAIL/UNKNOWN；golden 用了 SUSPECTED/BLOCK | 以 spec 为准 | spec 明确「冲突以本提示词为准」 |
| D4 | 前端定位 | 演示界面，交互简单易懂 | 用户强调最终用于展示 |
| D5 | 前端视觉风格 | 参考 Claude 主页（暖米白 + 陶土橙、大留白、克制） | 用户指定；实现时调 ui-ux-pro-max skill 拿细则 |

### D3 落地细则

- golden `VERSION-001: SUSPECTED` → 引擎产出 `FAIL` + `needs_human_review=true`
- golden `EVIDENCE-001: BLOCK` → 引擎产出 `UNKNOWN`（证据门禁阻断该 Finding 输出，不作为独立 RuleResult 状态）
- 同步修正 `golden_cases_demo.jsonl` 期望值，原文件备份为 `*.orig`，偏差写入 `docs/`

---

## 3. 目录结构

```
review/
├─ pyproject.toml
├─ README.md
├─ .gitignore
├─ scripts/
│  ├─ start.bat            # 独立进程启动 API
│  └─ seed_demo.py         # 从示例数据包导入规则/术语/规范
├─ app/
│  ├─ main.py              # FastAPI app 装配
│  ├─ settings.py
│  ├─ domain/              # 单一真相源
│  │  ├─ enums.py          # RuleStatus/ReviewStatus/Severity/Origin/OnMissing
│  │  ├─ schemas.py        # SourceSpan/ParameterFact/RuleDefinition/RuleResult/Finding
│  │  └─ exceptions.py
│  ├─ persistence/
│  │  ├─ database.py       # SQLite + SQLAlchemy 2.x
│  │  ├─ models.py
│  │  └─ repositories.py
│  ├─ storage/
│  │  ├─ paths.py          # 路径穿越防护
│  │  └─ hashing.py        # 文件 sha256
│  ├─ parsers/
│  │  ├─ base.py           # ParsedDocument 契约
│  │  ├─ docx.py           # 段落/标题/表格 XML 顺序迭代
│  │  └─ source_span.py    # 章节路径/段落index/表格行列定位
│  ├─ extraction/
│  │  ├─ sections.py       # Heading → section_path
│  │  ├─ parameters.py     # 正则扫段落 + 结构化读表格
│  │  ├─ terminology.py    # 别名归一
│  │  └─ normalization.py  # pint 单位换算 + 值规范化
│  ├─ rules/
│  │  ├─ loader.py         # YAML → RuleDefinition
│  │  ├─ engine.py         # 三值执行器
│  │  ├─ operators.py      # 10 个白名单 operator(纯函数)
│  │  ├─ selectors.py      # section 选择器
│  │  └─ evidence.py       # 证据门禁
│  ├─ diff/
│  │  ├─ pairing.py        # 文件配对评分
│  │  └─ parameter_diff.py # 参数差异 ADDED/REMOVED/CHANGED/UNCHANGED/UNKNOWN_SCOPE
│  ├─ review/
│  │  ├─ pipeline.py       # 流水线编排(状态机)
│  │  ├─ reconciliation.py # 规则+LLM 合并
│  │  └─ deduplication.py  # 相同规则/参数/Span 去重
│  ├─ llm/
│  │  ├─ interface.py      # LLMProvider 抽象
│  │  └─ adapters/mock.py  # 确定性 Mock
│  ├─ reports/
│  │  ├─ excel.py          # openpyxl 多工作表
│  │  ├─ word.py           # python-docx 报告
│  │  └─ anonymous.py      # 匿名结果包
│  ├─ security/
│  │  ├─ secrets.py        # keyring 封装
│  │  └─ audit.py          # 结构化日志(不落 key/请求体)
│  └─ api/
│     ├─ cases.py documents.py runs.py findings.py exports.py
│     └─ rules.py knowledge.py
├─ web/                    # 静态前端(HTML+Tailwind CDN+原生JS)
│  ├─ index.html
│  ├─ app.js
│  └─ styles.css
├─ configs/
│  ├─ rules/               # 从示例数据包导入
│  ├─ terminology/
│  └─ standards/
├─ storage/                # gitignore: cases/reports/logs/db/recycle_bin
└─ tests/
   ├─ unit/                # 解析/抽取/单位/每 operator 三值
   ├─ golden/              # DEMO 回归 + 反向不误报
   ├─ contract/            # LLM Mock 契约
   └─ security/            # 路径穿越/key 不落盘
```

原始文件、key、db、索引、报告、日志不进 git。

---

## 4. 核心数据模型

### 4.1 枚举（spec §7.3 权威）

```
RuleStatus   = PASS | FAIL | UNKNOWN
ReviewStatus = pending | confirmed | rejected | modified | resolved
Severity     = high | medium | low
Origin       = rule | llm | hybrid | human
OnMissing    = unknown | fail | block
```

`SUSPECTED` 不是 RuleStatus；`BLOCK` 仅表示输出门禁，不是 RuleStatus。

### 4.2 SourceSpan

DOCX 无稳定页码 → 用 `section_path` + `paragraph_index` + 表格 `table_index/row_index/column_index` 定位。字段：`span_id, document_id, section_path[], block_type(paragraph|table_cell|heading), paragraph_index, table_index, row_index, column_index, char_start, char_end, text, text_hash`。不伪造页码。

### 4.3 ParameterFact

```
fact_id, canonical_name, raw_name, raw_value, normalized_value,
raw_unit, canonical_unit, subject, time_scope, statistical_scope,
condition, source_document, source_version, source_span_id,
extraction_method(regex|table), confidence, human_status
```

**比较键** = `canonical_name + subject + time_scope + statistical_scope + condition`。任一关键维度缺 → 该比较输出 `UNKNOWN`。统一用 `time_scope`（非 `period_or_stage`）。规范名统一 `总设计产能`，`总产能/设计产能` 入别名。

### 4.4 RuleResult / Finding

见 spec §7.4 / §7.5。Finding 证据必须引用真实 span_id，禁止模型编造 Citation/Span/页码/条款号。

---

## 5. 审查流水线（状态机）

```
CREATED → VALIDATING_FILES → PAIRING_FILES → PARSING → BUILDING_SPANS
→ EXTRACTING_PARAMETERS → NORMALIZING_FACTS → RUNNING_RULES
→ RETRIEVING_KNOWLEDGE → CALLING_MODEL → VALIDATING_MODEL_OUTPUT
→ MERGING_FINDINGS → WAITING_HUMAN_REVIEW → COMPLETED
```

（本次 OCR_OR_VISION 跳过；RETRIEVING_KNOWLEDGE 用关键词占位。）

合并规则：规则问题直接入候选；LLM 问题过证据门禁；相同规则+参数+Span 去重；不同根因独立保留；人工确认不覆盖原始 AI 结果（存差异）。

---

## 6. 规则引擎 — operator 白名单（禁 eval）

每个 operator 是纯函数 `(facts, spans, cfg) → RuleResult`。10 个：

| operator | rule_id | 判定要点 |
|---|---|---|
| `required_sections_exist` | COMPLETENESS-001 | 缺→FAIL(on_missing:fail) |
| `required_parameter_table_exists` | COMPLETENESS-002 | 缺→UNKNOWN |
| `all_equal` | CONSISTENCY-001 | 值不等→FAIL；比较键缺→UNKNOWN |
| `sum_equals` | CONSISTENCY-002 | 分量≠总→FAIL；缺项→UNKNOWN |
| `product_approximately_equals` | CONSISTENCY-003 | 超相对容差0.05→FAIL |
| `less_or_equal` | CAPACITY-001 | 高峰>处理能力→FAIL |
| `change_requires_reason` | VERSION-001 | 变化无说明→FAIL+人工(原 SUSPECTED) |
| `issue_response_status_exists` | VERSION-002 | 无闭环状态→FAIL |
| `alias_normalization` | TERM-001 | 未归一→FAIL(low) |
| `evidence_required` | EVIDENCE-001 | 无证据→门禁阻断(该 Finding 转 UNKNOWN/人工) |

**关键实现难点**：参数抽取须同时命中正文段落（问题句，如「钻井总数为38口」）与结构化表格（附件A 关键参数表 36），两处各生成独立 SourceSpan，冲突才可被 `all_equal` 抓到。这是 DEMO-002 井数 36/38/36 的成败点。

> 更新（2026-07-15）：实现阶段在此 10 个之外新增 2 个仓库自有通用 operator——`reply_table_status_complete`(COMPLETENESS-003) 与 `prose_alias_unnormalized`(TERM-002)，共 12 个。跨参数算术 operator 不要求不同操作数共享 scope。DEMO-002 井数 36/38 的正文冲突经确认属于抽取限制（正文以非规范名抽取、无 scope），未由规则伪造。权威清单以 `app/rules/operators.py` 和 `docs/golden-status-deviation.md` 为准。

---

## 7. 对抗审查原则（测试策略）

第一性原理 + 对抗审查落地：

1. **每 operator 三值单测**：PASS/FAIL/UNKNOWN 各一例
2. **正向金标准**：DEMO-002≥5类、DEMO-003≥6类、DEMO-004≥6类，真实发现
3. **反向不误报**（对抗）：
   - G-005 时间 scope 不同 → CONSISTENCY-001 = UNKNOWN，不报冲突
   - G-006 统计口径不同 → UNKNOWN
   - G-007 单位可换算 → PASS
   - G-008 证据不足 → 转人工
   - 历史意见不冒充规范
4. **不改期望值迁就实现**：引擎抓不到就修引擎；确因 spec/golden 冲突(D3)才改期望，且记录备份
5. **证据完整率 = 100%**：有效问题必须带真实 span 证据，否则门禁拦截

诚实报告：验收指标（解析成功率、Precision/Recall）用金标准实测填写，未跑的标「未跑」，不伪造。

---

## 8. 前端设计

**技术**：FastAPI 提供 API + 静态 `web/`（HTML + Tailwind CDN + 原生 JS fetch），无构建步骤，单进程，只监听 `127.0.0.1`。

**定位**：演示界面 — 交互简单直白、易操作。

**视觉**（参考 Claude 主页，实现时调 ui-ux-pro-max skill 拿细则）：
- 暖米白底 `#F5F1EB`，陶土橙强调 `#D97757`，中性灰文字
- 大留白、圆角卡片、克制无花哨动效
- 衬线标题 + 无衬线正文

**页面**：
1. 仪表盘：案例列表、运行状态
2. 案例：新建 + 多文件上传 + 配对确认
3. 审查结果：参数事实表 / 规则结果 / Finding 卡片（含证据原文引用、规范依据）
4. 专家复核：确认 / 驳回 / 改标题·描述·建议·严重度 / 备注
5. 导出：Excel / Word / 匿名包

页面全程显示「AI 初审结果，不是正式审查结论」。

---

## 9. LLM 接口与安全

**接口**：`LLMProvider` 抽象（`generate_structured / health_check / capabilities / count_tokens`）。本次 `MockProvider` 产出确定性、过证据门禁的结构化输出。真适配器（Anthropic Messages / OpenAI 兼容）留占位，页面配置，key 走 keyring。业务层只依赖抽象接口，不碰厂商 SDK 对象。

**安全**：
- API key 仅存 Windows Credential Manager（keyring），禁入 SQLite/YAML/日志/报告/匿名包
- 日志不落 key、不落完整请求体
- 文档内容中的指令视为数据，不作系统指令
- 文件类型白名单、文件名路径穿越防护、单文件 100MB、300 页上限
- 匿名包剔除厂商、Model ID、Base URL、Request ID、key
- 删除 → 回收站 → 二次确认永久删除

---

## 10. 技术选型（锁版本，Python 3.12.10）

本次依赖：
```
fastapi uvicorn pydantic>=2 sqlalchemy>=2
python-docx openpyxl pandas pint pyyaml rapidfuzz
keyring httpx structlog
pytest pytest-asyncio hypothesis
```

延后依赖（留 extras）：`pymupdf sentence-transformers paddleocr anthropic openai`

---

## 11. 验收演示路径（本次可达）

导入示例规则/术语/规范 → 新建案例 → 上传 DEMO-002 → 解析 → 看参数事实 → 看规则结果 → 看 DEMO-002 多个问题及证据 → 确认/驳回/改/备注 → 导出 Excel/Word/匿名包 → DEMO-003 双版本配对 + 差异 → 金标准回归全绿。

未达（延后）：真实在线 LLM 连通、PDF/OCR、向量检索、与 AI 中台盲评。
## 当前实现边界（2026-07 收口）

Anthropic 适配器已经实现，但默认 Provider 仍为 No-op Mock，在线 LLM 仅在用户明确配置后启用；公共端点默认要求 HTTPS，私网模式必须显式打开。当前输入范围是文本型 DOCX，PDF、OCR、RAG、知识图谱未实现。`app/diff/` 等库能力存在但未接入 Web 历史界面，DEMO_ONLY 规则包也不代表 42 章正式规则。页面输出是 AI 初审结果和规则结果，不是正式审查结论；在线使用必须先脱敏并完成组织审批。正式启动仅支持 loopback，不提供公网认证或公网部署承诺。
