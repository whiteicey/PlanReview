# 开发方案审查助手（本地版）· PlanReview

面向油气田**开发方案 DOCX** 的本地自动初审工具。它在明确的上传大小、ZIP 解压资源和文档结构限制内，把 Word 方案解析成带来源定位的参数事实，用三值规则引擎和可选 LLM 复核生成可追溯问题清单。DOCX 页数无法由 python-docx 可靠计算，因此本工具不作页数上限承诺。

> **AI 初审结果，不是正式审查结论。** 所有输出都必须由具备资质的专家复核后才能采信；本工具不做自动审批、不签发正式结论。

- **本地优先 / 隐私**：服务只监听 `127.0.0.1`，方案文件不出本机（除非你主动配置在线 LLM）。
- **可追溯**：每个问题都指向原文的章节 / 段落 / 表格行列。
- **诚实**：证据不足只报 UNKNOWN 转人工，绝不把「没发现问题」说成「方案正确」。

---

## 快速开始

要求 **Python 3.12**。

```bash
# 1. 克隆
git clone git@github.com:whiteicey/PlanReview.git
cd PlanReview

# 2. 安装（可编辑安装 + 开发依赖）
python -m pip install -e ".[dev]"

# 3. 跑测试（应全绿，示例规则包已随仓库提供）
python -m pytest -q

# 4. 启动本地服务
python scripts/run_local.py
# 浏览器打开 http://127.0.0.1:8765
```

在页面上「选择 DOCX → 上传并开始」，即可看到问题卡片。可上传随仓库附带的示例方案
`本地版示例数据包/plans/DEMO-004_综合缺陷方案_V1.0.docx` 试跑。

面向**非技术使用者**的详细图文操作手册见 **[docs/使用手册.md](docs/使用手册.md)**。

---

## 它能查什么（12 条规则）

三值状态只有 `PASS / FAIL / UNKNOWN`。规则 operator 白名单在 [app/rules/operators.py](app/rules/operators.py) 的 `OPERATOR_NAMES`。

| 类别 | 规则 | 举例 |
|------|------|------|
| 完整性 | COMPLETENESS-001/002/003 | 缺必备章节；缺关键参数表；审查意见回复表漏填状态 |
| 一致性 | CONSISTENCY-001 | 开发井总数在摘要/正文/表格应一致 |
| 汇总核对 | CONSISTENCY-002/003 | 生产井+评价探井=开发井总数；井数×单井产能≈总设计产能 |
| 跨专业 | CAPACITY-001 | 高峰产量不应超过地面处理能力 |
| 版本变更 | VERSION-001/002 | 关键参数变化需说明原因；上一轮意见应有回复状态 |
| 术语 | TERM-001/002 | 参数别名应归一（事实层 + 正文层） |
| 证据 | EVIDENCE-001 | 每个问题必须关联真实原文证据，否则门禁拦截 |

其中示例包内 10 条来自 `本地版示例数据包/rules/ruleset-demo-0.1.yaml`；仓库自有 2 条
（COMPLETENESS-003、TERM-002）在 [app/rules/repo_rules.yaml](app/rules/repo_rules.yaml)。

---

## 架构与处理流水线

```
DOCX 上传
  → 解析（段落/标题/表格，按 XML 顺序，生成 SourceSpan 定位）
  → 参数事实抽取（正则扫正文 + 结构化读表格）
  → 术语归一 + 单位换算（pint）
  → 三值规则引擎（10 个纯函数 operator + 证据门禁）
  → LLM 复核（Mock 或在线 Anthropic，过证据门禁；失败则 fail-closed 跳过）
  → 合并去重 → Finding（带可读位置）
  → 专家复核（确认/驳回/已解决 + 备注，不覆盖 AI 原始结果）
  → 导出 Excel / Word / 匿名包
```

目录结构：

```
app/
  domain/       枚举 / Pydantic schema / 异常（单一真相源）
  parsers/      DOCX 解析 + SourceSpan
  extraction/   参数抽取 / 术语归一 / 单位规范化
  rules/        loader / engine / operators / evidence / ruleset(加载器)
  diff/         多版本文件配对 + 参数差异
  review/       pipeline(流水线) / reconcile(合并去重)
  llm/          provider(抽象) / mock / adapters(anthropic) / factory / config_store
  reports/      excel / word / anonymous 导出
  persistence/  SQLite + SQLAlchemy 2.x
  security/     credentials(keyring) / url_policy / logging
  api/          FastAPI 路由 + schema
web/            静态前端（HTML + 原生 JS，暖色主题）
scripts/        run_local.py(启动) / import_demo.py(校验选定 DOCX 并读规则)
本地版示例数据包/  DEMO_ONLY 虚构示例：规则/术语/标准/golden/示例方案
tests/          unit / golden(DEMO回归+反向不误报) / contract(API/LLM/import) / security
docs/           设计、使用手册、golden 偏差、在线 LLM 方案、验收模板
```

---

## 命令速查

```bash
python -m pytest -q                    # 全量测试（示例包在仓库内，golden 自动跑）
python -m pytest -q tests/security     # 只跑安全测试（注入/路径/密钥/脱敏）
python scripts/run_local.py            # 启动，仅监听 127.0.0.1:8765
python scripts/import_demo.py <a.docx> # 校验选定 DOCX + 读示例规则/术语（不复制源文件）
```

环境变量（都可选）：

| 变量 | 作用 | 默认 |
|------|------|------|
| `REVIEW_DEMO_ROOT` | 覆盖示例规则包位置 | 自动发现仓库内 `本地版示例数据包/` |
| `REVIEW_STORAGE_ROOT` | 案例/报告/数据库存储根 | `storage/` |

---

## 可选：接入在线 LLM 复核

默认使用内置 **Mock**（确定性、不联网）。如需更强的 AI 复核，在页面「AI 复核设置（可选）」里配置：

- **复核方式** = 在线 LLM（Anthropic 格式）
- **Base URL** = Anthropic 兼容网关（默认预填 `https://api.deepseek.com/anthropic`）
- **模型** + **API Key** → 点「保存配置」「测试连接」

**密钥安全**：API Key 只存入操作系统凭据库（Windows Credential Manager / keyring），**绝不**写入
文件、数据库、日志、导出报告或 Git。配置文件 `storage/llm_config.json` 只存 provider/base_url/model。

> ⚠️ 一旦启用在线 LLM，方案正文会发送到你配置的网关。**只允许上传已脱敏的方案**；涉密/未脱敏文件严禁上传，也严禁用在线模型处理。设计与实现细节见
> [docs/online-llm-adapter-plan.md](docs/online-llm-adapter-plan.md)，正式投产前建议再做一次安全审查。

---

## 贡献指南

- **测试驱动**：改逻辑先写/改测试再改实现；提交前 `python -m pytest -q` 全绿。
- **红线（务必遵守）**：
  1. **第一性原理**——Finding 必须由真实事实 + 证据经**通用规则**推导；**禁止** grep 文档正文里的结论字符串来「发现」问题，**禁止**按 rule_id / 参数名硬编码特判（历史上出现过 `legacy_compatibility` 作弊，已删，`tests/unit/test_compatibility_safety.py` 守门）。
  2. **对抗性测试**——每个 operator 有 PASS/FAIL/UNKNOWN 单测 + 反向不误报断言；不改期望值迁就实现，引擎抓不到就修引擎，或诚实缩小 golden 并在 [docs/golden-status-deviation.md](docs/golden-status-deviation.md) 记录原因。
  3. **fail-closed**——UNKNOWN / 缺证据 / 解析失败 / 在线 LLM 失败都不得静默变 PASS。
  4. **安全**——密钥只进 keyring；服务只绑 loopback；禁 `eval`/`exec`；文档内容一律当数据，绝不当指令执行；匿名包剔除厂商/Model/Base URL/Request ID/key 与可读位置。
  5. **仅文本型 DOCX**——PDF/OCR、向量 RAG 刻意延后，用户明确提出前不做；遇非 DOCX 明确报错。
- **诚实报告**：验收指标用实测填写，未跑标「未执行」，禁用未实测的「准确率/召回率/节省时间/成本」宣传。

---

## 边界与免责

示例规则、术语、标准、历史意见、golden、示例方案均为 **`DEMO_ONLY` 虚构演示数据**，仅供开发与试用，**不构成任何正式审查依据**。用于实际业务前，须由专家建立经确认的正式规则集，不得与演示数据混用。

页面、Excel、Word、匿名包全程保留「**AI 初审结果，不是正式审查结论**」。

---

## 路线图

后续增强想法（待实施）见 **[docs/future-ideas.md](docs/future-ideas.md)**：LLM 复核反馈展示、方案审查历史记录、一次上传≤3 份方案做版本比对。
## 当前能力边界（02f3012 后收口）

- 已实现 Anthropic Messages REST 适配器；默认仍是 No-op Mock，在线 LLM 必须显式 opt-in。
- 在线端点默认只允许公共 HTTPS；私网/本机端点只有勾选显式开关后允许，且材料必须先脱敏。
- 当前只支持文本型 DOCX；PDF、OCR、RAG 和知识图谱未实现。
- `app/diff/` 等库代码存在但未接入 Web 历史界面；演示规则集不是“42 章”正式规则库。
- 页面结果是 AI 初审/规则结果，不是正式审查结论；在线调用前需完成脱敏和组织审批。
- 正式启动脚本仅支持 loopback（`127.0.0.1`/`::1`）；第三方直接调用 uvicorn 不等于受支持的公网部署方案。
