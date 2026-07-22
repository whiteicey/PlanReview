# PlanReview 可靠性与安全修复日志

更新日期：2026-07-17
目标分支：`fix/full-reliability-hardening`
修复前基准：`d808d8c`（远端主线快照）
当前代码版本：`02f3012`

## 1. 修复概览

本轮工作分为三个连续阶段：

1. P0 可信度和安全修复：失败审查不得表现为成功、默认 Mock 不生成业务问题、算术比较必须统一单位。
2. ReviewRun 追加式历史：同一案例保留多次审查记录，并把专家复核严格绑定到具体 Run。
3. 剩余已知缺陷加固：Excel、DOCX、持久化脱敏、规则判断、数据库生命周期、分类体系、专家备注、LLM 边界、版本识别和文件补偿共十组修复。

修复前全量基线为 Python 3.12.13、`367 passed`，Node `2 passed`。修复后为：

- 全量 Python：`432 passed, 1 skipped`
- Unit：`269 passed, 1 skipped`
- Contract：`58 passed`
- Security：`90 passed`
- Node：`2 passed`

唯一跳过项是当前 Windows 测试环境没有创建符号链接的权限；其他路径穿越和存储边界测试均已执行。

## 2. P0：审查失败不再伪装成成功

对应提交：`9bb9cea`

### 修复前

- Pipeline 发生异常后虽然把 Run 标记为 `FAILED`，API 仍可能返回 HTTP 201。
- 前端把失败 Run 的空 findings 当作“未发现问题”。
- 失败 Run 仍可能显示导出按钮和专家复核控件。
- 获取失败 Run 的 findings 会返回空数组，调用方无法区分“没有问题”和“审查没有完成”。
- 内部异常信息存在被直接传到响应的风险。

### 修复后

- 审查接口先持久化 Run，再检查 `final_status`。
- 失败 Run 返回 HTTP 422，并提供：`case_id`、`run_id`、`final_status`、实际 `failed_stage` 和固定的用户说明。
- `failure_detail` 不包含 Python 异常类型、文件路径、数据库信息、请求正文、API Key 或 Token。
- 失败 Run 获取 findings 返回 HTTP 409。
- 失败或未进入人工复核状态的 Run 不允许导出。
- 前端失败状态显示“本次审查未完成”，同时隐藏“未发现问题”、导出按钮和专家复核控件。
- 前端状态判断被抽成纯函数，并使用 Node 内置测试框架验证。

## 3. P0：默认 MockProvider 改为真正的 No-op

对应提交：`9bb9cea`

### 修复前

默认 Mock 会扫描正文关键词。例如同时出现“高峰产量”和“超过处理能力”就生成高风险 finding，导致“高峰产量不超过处理能力”也可能被误报。

LLM finding 还存在证据范围过宽的风险：一个默认 finding 可能无条件关联输入中的全部 spans。

### 修复后

- 默认 `MockProvider.review()` 始终返回经过结构校验的空 findings。
- Mock 仅用于验证调用链，不再承担业务判断。
- 确定性业务 finding 由测试代码中的 `FakeProvider` 生成，生产默认 Provider 不引用 Fake。
- LLM 输入改为逐 span 的固定格式：

  ```text
  [span_id]
  span text
  ```

- LLM finding 只能引用真实存在、实际发送给 Provider 的 span。
- 编造 span 或非法 finding 结构会在证据校验阶段失败，不会降级为合法 finding。
- `LLMProviderError` 与证据校验失败分离：网络、401、429、500 等 Provider 错误保留规则结果；非法 evidence 可以使 `LLM_REVIEWED` 阶段失败。

## 4. P0：流量单位统一后再比较

对应提交：`9bb9cea`

### 修复前

- `亿m³/a` 和 `万m³/d` 可能被保留在不同时间尺度。
- `less_or_equal` 等算术 operator 直接比较裸数值，可能把数量“口”和流量 `m³/d` 放在一起比较。
- 单位缺失或不兼容时，结果可能缺少明确的 UNKNOWN 语义。

### 修复后

- 体积流量统一为 `m^3/day`。
- 年度流量明确按 `1 year = 365 days` 换算。
- 支持 `亿m³/a`、`亿m3/a`、`万m³/d`、`万m3/d`、`m³/d` 和 `m3/d`。
- 单位兼容性基于单位类别和物理维度，不按某个中文单位写死业务分支。
- `口` 注册为 `count`；未来增加“座、台、套”只需扩展单位注册表。
- `less_or_equal`、`sum_equals` 和 `product_approximately_equals` 遇到缺失或不兼容单位时返回 UNKNOWN，不把 UNKNOWN 改成 PASS。
- 浮点运算使用 `math.isclose` 和明确容差。

典型结果：

- `10 亿m³/a ≈ 2,739,726.03 m³/d`
- `2,739,726.03 m³/d <= 3,000,000 m³/d` 为 PASS
- `4,000,000 m³/d <= 3,000,000 m³/d` 为 FAIL
- `10 口 <= 3,000,000 m³/d` 为 UNKNOWN

## 5. ReviewRun 改为追加式历史

对应提交：`3f508be`

### 修复前

- 一个 Case 实际上只能稳定保存一个 ReviewRun。
- 重新审查可能覆盖旧 Run、findings 或专家复核状态。
- 默认查询容易被最新失败 Run 遮挡，上一轮成功结果无法作为稳定结果继续使用。
- 专家复核写入可能缺少明确的 Run 归属。
- LLM Provider、Model、状态和 finding 数量没有形成完整的 Run 级审计元数据。

### 修复后

- 每次审查创建新的 UUID4 `run_id`，旧 Run 不更新、不删除。
- 旧数据库中的单 Run 使用固定 namespace 的 UUID5：

  ```python
  uuid.uuid5(
      LEGACY_REVIEW_RUN_NAMESPACE,
      f"review-run:{case_id}",
  )
  ```

- 同一旧 case 重复迁移得到相同 ID，不同 case 得到不同 ID，迁移可重复执行且不增加记录。
- RuleResult、Finding、专家状态、备注和复核时间均绑定内部 `review_run_id`，API 使用标准 UUID `run_id`。
- 默认 findings 和导出读取“最新成功 Run”；最新失败 Run 不会遮挡上一轮成功结果。
- 新增正式嵌套路由：

  ```text
  GET   /api/cases/{case_id}/runs
  GET   /api/cases/{case_id}/runs/{run_id}
  GET   /api/cases/{case_id}/runs/{run_id}/findings
  PATCH /api/cases/{case_id}/runs/{run_id}/findings/{finding_id}
  ```

- 专家复核必须同时校验 `case_id + run_id + finding_id`。跨 Case 或跨 Run 返回 404，且不修改数据。
- 旧写入口缺少 `run_id` 时返回明确错误，不再猜测最新 Run。
- Run 持久化 `llm_provider`、`llm_model`、`llm_status`、`llm_finding_count` 和脱敏错误摘要。

### 数据库迁移差异

旧表重建在 SQLite 事务内执行，保留父子主键、外键、专家状态、备注和时间。迁移会检查父表及子表行数、`run_id` 唯一性、索引和 `PRAGMA foreign_key_check`；任一校验失败整体回滚，可修复后重试。

## 6. Excel 公式注入防护

对应提交：`52c037f`

### 修复前

Finding 标题、描述、建议、专家备注、规则说明、证据文本、位置和文件名等外部可控文本没有统一的 Excel 单元格安全入口。以 `= + - @` 开头的内容可能被表格软件解释为公式。

### 修复后

- 所有外部文本统一通过 `safe_excel_cell`。
- 非法控制字符被清理。
- 危险起始字符增加前导单引号。
- 数字、日期和布尔值保持原类型，不被无条件字符串化。
- 当前导出明确不使用系统公式，因此最终扫描拒绝任何公式单元格。
- openpyxl 回读测试验证攻击载荷的 `data_type != "f"`，并扫描整个工作簿确保零公式。

## 7. DOCX ZIP 与解析资源限制

对应提交：`dae6ac5`

### 修复前

- 上传主要依赖扩展名和后续解析，缺少完整 ZIP 预检。
- ZIP 炸弹、重复规范名、异常压缩方式、加密成员、路径穿越和危险嵌入对象的处理不完整。
- 页面数承诺无法可靠约束 DOCX 解析资源。

### 修复后

- 上传前检查成员数、单成员大小、总解压大小、压缩比、空名、重复名、绝对路径、盘符、反斜杠、`..`、加密和压缩方式。
- 要求 DOCX 核心成员存在。
- 不采用封闭的 Office 顶层目录 allowlist；未知但路径安全的 Office 扩展目录允许通过。
- 宏、ActiveX、OLE 和嵌入包明确按不支持处理。
- 在 python-docx 前流式扫描 `word/document.xml`，限制字符、段落、表格和真实 XML 单元格数量。
- 资源超限固定返回 413；结构和路径异常固定返回 422；错误不回显 ZIP 成员名。
- 删除 `max_pages` 和“最大 300 页”表述，改为可验证的大小与结构限制。

## 8. 持久化元数据脱敏改为精确规则

对应提交：`fcf55dc`

### 修复前

递归清理使用过宽的 substring 判断，可能误删合法业务元数据，也可能难以解释哪些字段允许进入数据库。

### 修复后

- statistics 只允许：`document_count`、`response_status`、`response_sections`、`request_count`、`rule_count`、`fact_count`、`finding_count`。
- 未知统计字段 fail-closed。
- details 和快照使用精确 denylist 与危险后缀，不再匹配任意 substring。
- 明确过滤 key、token、authorization、请求/响应正文、raw prompt/response、secret 和 password。
- 普通业务文本中的“token”等词不会仅因 substring 被删除。
- 增加数据库关闭重开后的往返测试，确认安全业务字段被保留、敏感字段不会重新出现。

## 9. 变更原因按完整表格行判断

对应提交：`93d9d6c`

### 修复前

规则可能按单个表格单元格或固定列位置判断变更原因，列顺序变化、多表、多行或合并单元格会导致漏判。

### 修复后

- 按 `document_id + table_index + row_index` 聚合完整表格行。
- 按底层单元格身份去除合并单元格的重复引用。
- 使用表头词识别参数、变更前值、变更后值和原因列，不猜测固定列序。
- 所有命中参数的行都必须提供非空且非占位词的原因；任一缺失即 FAIL。
- 存在相关表格但无法识别可靠表头时返回 UNKNOWN。
- 没有相关表格时保留普通段落兼容逻辑。

## 10. 数据库 Engine 和 Session 生命周期

对应提交：`b4ba3e3`

### 修复前

数据库 Engine 或 Session 可能在请求间共享或重复创建，启动升级次数和异常关闭行为不够明确。

### 修复后

- FastAPI lifespan 创建一个应用级 Engine/sessionmaker。
- 建表和迁移只在应用启动时执行一次。
- 每个请求获取独立 Session，正常或异常退出都关闭。
- 禁止共享全局 Session。
- SQLite 启用 `check_same_thread=False`、外键检查和 5000ms `busy_timeout`。
- 测试仍可注入独立临时数据库，原 ReviewRun 迁移事务语义保持不变。

## 11. Finding 分类体系统一

对应提交：`871f752`

### 修复前

Rule、Finding、LLM、YAML、API、前端和导出可能接受不同分类字符串，非法分类可能在某些入口通过、在另一些入口失败。

### 修复后

- 增加共享 `FindingCategory` 枚举，固定 11 个合法英文分类。
- Rule、Finding、LLM 校验、API 和导出统一使用该枚举。
- 仅保留两个兼容映射：`version-change → version_change`、`unknown → unknown_scope`。
- 其他非法值 fail-closed。
- 旧数据库读取时完成兼容映射，后续写入 canonical 值。
- 匿名导出按固定枚举顺序生成稳定别名。

## 12. 专家备注校验统一

对应提交：`16e12bd`

### 修复前

- 前端、API 和 Repository 的长度或敏感词规则可能不一致。
- 普通业务词“token”存在被误判为密钥的风险。
- FastAPI 默认校验错误可能携带原始 input。

### 修复后

- 前端、API、Repository 统一允许最多 4000 个 Python 字符。
- 3999 和 4000 字通过，4001 字拒绝。
- 多段中文和普通业务词“token”允许保存。
- 仅拒绝明确密钥格式、Authorization header 和明显完整请求/响应转储。
- 错误响应使用固定脱敏说明，不回显备注。
- FastAPI 校验错误移除原始 `input` 和上下文。

## 13. LLM 配置失败显式化并限制输入

对应提交：`95590dc`

### 修复前

- 在线 Provider 配置缺 Key、Base URL 或 Model 时可能回退 Mock，使调用方误以为在线配置已经生效。
- LLM 输入缺少统一 span 数、单 span 字符数、总字符数和 evidence ID 上限。
- 超限时缺少“部分发送”和“完全无法发送”的区别。

### 修复后

固定七种状态：

```text
NOT_RUN
COMPLETED
COMPLETED_PARTIAL
CONFIGURATION_ERROR
PROVIDER_ERROR
INPUT_LIMIT_EXCEEDED
VALIDATION_FAILED
```

共享边界：

```text
MAX_LLM_SPANS = 200
MAX_LLM_TOTAL_CHARACTERS = 80_000
MAX_LLM_SINGLE_SPAN_CHARACTERS = 4_000
MAX_LLM_EVIDENCE_IDS = 200
```

- 字符数统一按 Python `len()`。
- 按稳定文档顺序选择 spans；单 span 截取前 4000 字，并在 span、evidence 和序列化总字符限制内依次装入。
- 完整发送为 `COMPLETED`；存在截断或遗漏但调用和校验成功为 `COMPLETED_PARTIAL`。
- 没有任何有效 span 可发送时不调用 Provider，状态为 `INPUT_LIMIT_EXCEEDED`。
- finding 只能引用实际发送集合中的 evidence ID。
- 在线配置不完整或凭据存储不可用时为 `CONFIGURATION_ERROR`，保留规则 findings，Run 仍可进入人工复核。
- Provider 网络错误为 `PROVIDER_ERROR`；非法 evidence 为 `VALIDATION_FAILED`，且不保存非法 LLM finding。
- API Key 不进入配置文件、响应、日志或数据库；增加凭据清除接口，切回 Mock 时清除旧在线凭据。

## 14. 来源版本识别和合并单元格去重

对应提交：`0777c3f`

### 修复前

- `V 1.0`、`V1.0.2`、`第 2.1 版` 等格式识别不完整。
- 普通数字存在被误认为版本号的风险。
- 合并单元格可能产生重复 SourceSpan；仅按文本去重又会误删不同真实单元格中的相同文本。

### 修复后

- `V1`、`v1`、`V 1.0`、`V1.0.2`、`第1版`、`第 2.1 版` 统一规范为完整 `V…`。
- 未知格式返回 `None`。
- 使用边界约束避免普通数字误识别。
- 表格按底层 XML cell 身份去重，保留第一次出现的稳定 span ID。
- 相同文本但属于不同真实单元格时继续保留。
- 正文和表格遍历顺序不变。

## 15. 案例文件创建和删除补偿

对应提交：`02f3012`

### 修复前

- 上传文件可能先进入正式目录，随后数据库提交失败，形成孤儿文件。
- 永久删除可能先删数据库再逐个删文件；中途失败时难以恢复一致状态。
- 文件补偿失败缺少持久化审计和启动告警。

### 修复后

创建流程：

1. 文件写入 storage 内 staging。
2. 完成 ZIP 和 DOCX 解析校验。
3. 原子移动到正式案例目录。
4. 提交数据库。
5. 数据库失败时删除正式文件并清理空目录。

删除流程：

1. 确认案例已进入回收站。
2. 把案例文件和报告原子移动到 storage 内 quarantine。
3. 提交数据库永久删除。
4. 数据库失败则从 quarantine 恢复。
5. 数据库成功后清理 quarantine。

安全边界：

- 所有路径必须位于 storage 根目录内。
- 拒绝路径穿越、目录外操作和符号链接树。
- 删除前检查整个目标树，不跟随符号链接递归删除。
- 重复删除返回 409；登记文件已不存在时仍可安全完成数据库删除。

审计差异：

- 首选写入 append-only `file_operation_audit` 数据库表。
- 数据库审计失败时，追加到 `runtime/audit/file_operations.jsonl`。
- JSONL 每行一个紧凑 JSON，并执行 `flush()` 和 `os.fsync()`。
- 审计不保存绝对路径、正文、密钥或异常堆栈。
- 记录 `recovery_required`；启动时检测数据库和 JSONL 中的恢复事件并输出脱敏告警。
- 本轮只告警，不自动恢复。

## 16. 对外行为变化汇总

| 场景 | 修复前 | 修复后 |
|---|---|---|
| 审查阶段异常 | 可能返回 201 或显示空结果 | 持久化 FAILED Run，返回 422 |
| 失败 Run findings | 可能返回空数组 | 返回 409 |
| 默认 Mock | 可能按正文关键词生成问题 | 始终返回 0 条 LLM finding |
| 最新审查失败 | 可能遮挡旧成功结果 | 默认查询仍返回最新成功 Run |
| 专家复核 | 可能缺少 Run 归属 | 必须指定 Case、Run、Finding |
| 流量比较 | 可能比较裸数值 | 统一为 `m^3/day` 后比较 |
| 单位不兼容 | 结果不明确 | 返回 UNKNOWN |
| Excel 外部文本 | 可能触发公式 | 统一安全编码，工作簿零公式 |
| DOCX 异常包 | 主要依赖后续解析 | ZIP 和 XML 两级预检 |
| DOCX 超限 | 页面数承诺不可靠 | 固定资源限制，返回 413 |
| LLM 输入过大 | 状态和边界不完整 | 稳定截断，`COMPLETED_PARTIAL` |
| 在线配置缺失 | 可能回退 Mock | `CONFIGURATION_ERROR`，不伪装在线成功 |
| 创建 DB 失败 | 可能留下孤儿文件 | 删除正式文件并记录补偿事件 |
| 删除 DB 失败 | 文件与 DB 可能不一致 | quarantine 后恢复 |

## 17. 当前保留风险和范围外事项

- `recovery_required` 当前只检测和告警，不自动恢复。
- JSONL 使用进程内锁，适用于当前单进程本地应用；多 worker 部署需要跨进程文件锁。
- 真实在线 LLM 的网络和凭据由部署环境决定；本轮验证了配置错误、Provider 错误、证据校验和输入截断边界，没有保存或提交真实 API Key。
- Windows 当前测试环境无法创建符号链接，因此对应测试跳过；代码仍显式拒绝符号链接，建议在具备权限的 CI 环境补跑。
- PDF、OCR、RAG、知识图谱、版本 Diff 和复杂历史前端不在本轮范围内。

## 18. 提交链

```text
02f3012 fix: make case file persistence and deletion compensating
0777c3f fix: improve source version parsing and merged-cell deduplication
95590dc fix: make llm configuration failures explicit and bounded
16e12bd fix: align expert note validation across api and persistence
871f752 refactor: unify finding category taxonomy
b4ba3e3 refactor: manage database engine and sessions by application lifecycle
93d9d6c fix: evaluate change reasons using complete table rows
fcf55dc fix: preserve safe persistence metadata during sanitization
dae6ac5 fix: enforce docx archive and parser resource limits
52c037f fix: prevent spreadsheet formula injection
3f508be feat: add append-only review run history
9bb9cea fix: harden review failure handling and unit-safe comparisons
```
