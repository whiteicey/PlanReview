# PlanReview 剩余缺陷收口验收报告

## 范围与基线

- 基础提交：`02f3012`
- 工作分支：`fix/remaining-correctness-security-closeout`
- 执行顺序：阶段 A（规则/LLM 语义）→阶段 B（LLM 配置与前端状态）→阶段 C（文本、规则集路径与边界安全）→阶段 D（文档与目标环境验收）。
- Python：3.12.13（Windows，使用仓库配置的 bundled Python 运行时）。
- Node：v24.14.0（使用仓库配置的 bundled Node 运行时）。
- 修改前基线：Python `432 passed, 1 skipped, 1 warning`；Node `2 passed`。

由于本机 `python`/`node` 未加入 PATH，且 pytest 默认临时目录没有写权限，实际执行使用等价命令：

```text
C:\Users\连浚杰\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe
  -m pytest -q -p no:cacheprovider --basetemp C:\Users\连浚杰\Documents\AI培训\.planreview-test-tmp-20260718 ...
C:\Users\连浚杰\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe --test web/*.test.js
```

## 阶段 A

- 非法 LLM 输出不再使规则 Finding 丢失：保存 `VALIDATION_FAILED` 状态，清空非法 AI Finding，保留规则结果并继续完成 Run。
- 术语判断使用原始文档名称，别名命中不再被误判为规范名称。
- `TERM-001` 对全部术语条目展开，不再只取一个条目。
- `EVIDENCE-001` 通过事实的 `source_span_id` 校验证据，幽灵 span、无证据和无相关事实均保持 fail-closed。
- incomplete sibling 只在同一比较作用域内触发，避免把无作用域说明误当成同一参数。
- 章节匹配使用边界安全的标题 token，`3.1` 不匹配 `3.10`，同时支持真实规则标题。

## 阶段 B

- LLM 失败与规则结果隔离；配置错误、ProviderError、输入超限和非法 evidence 使用独立状态。
- 公共 LLM endpoint 默认严格要求 HTTPS；私网/loopback endpoint 只有显式开关开启后允许。
- 修改在线 endpoint 或私网模式必须重新输入 API Key；凭据清除接口保留，API Key 不进入配置、响应、日志或数据库。
- 前端显示明确 LLM 状态及 Finding 来源，不把配置失败或不完整 AI 结果显示成有效初审结果。

## 阶段 C

- Finding 标题、描述和建议允许有界多段文本，并继续拒绝明确凭据、Authorization、私钥、JWT 及完整请求/响应转储。
- Ruleset reload 只使用服务端活动规则集，不接受客户端路径。
- 支持的启动脚本只允许 loopback host。
- storage 路径检查拒绝符号链接父目录、符号链接文件、路径穿越及目录外操作；新增测试在 Windows 无 symlink 权限时安全跳过。

## 阶段 D 验收结果

### Python 测试

| 命令 | 实际结果 |
|---|---|
| `tests/unit` | `278 passed, 3 skipped, 1 warning` |
| `tests/contract` | `60 passed, 1 warning` |
| `tests/security` | `99 passed` |
| `tests/golden tests/contract`（`REVIEW_DEMO_ROOT` 指向本地示例包） | `75 passed, 1 warning` |
| 全量 `pytest -q` | `452 passed, 3 skipped, 1 warning` |

3 个 skip 均为 Windows 符号链接创建权限不足，位置是 `tests/unit/test_file_operation_compensation.py` 的父目录、文件和目录内 symlink 场景；测试未将 skip 报告为 pass。安全断言已保留，具备 symlink 权限的环境会执行这些场景。

### Node 测试

```text
web/*.test.js: 4 passed, 0 failed, 0 skipped
```

覆盖失败审查隐藏空结果/导出/专家复核、成功空结果状态、七种 LLM 状态和 Finding 来源展示。

### 静态检查

以下四项均无命中：

- 默认取单一术语条目的 `next(iter(terminology.canonical_to_aliases...))`；
- 通过 `len(context.spans)` 绕过 evidence 要求；
- `legacy_compatibility`；
- `eval`/`exec`。

`git diff --check` 无错误。

### 手工 smoke

使用 `python scripts/run_local.py` 启动 loopback 服务，并使用仓库 DEMO DOCX 完成 HTTP 烟测：

- `/api/health`：200；
- DOCX 上传：201；
- 正常审查：201，`READY_FOR_HUMAN_REVIEW`，默认 Mock 的 `COMPLETED`，0 条 AI Finding；
- 默认 findings：200；
- 成功 Run 的 `/api/cases/{case_id}/exports/xlsx`：200，返回 Excel media type。

失败 Run、配置缺失、非法 evidence、连续审查与规则 Finding 保留由 contract/unit/security 测试覆盖；本次没有把在线网络或真实 API Key 纳入烟测。

## 提交与工作区

阶段 A-D 的实现已按逻辑任务拆分为独立本地提交；阶段 D1 文档对齐提交后，将再次执行全量测试、`git diff --check`、`git status` 和最近提交检查。未执行 `git push`，未创建或合并 PR。

## 剩余风险

- 当前仍是本地 loopback 工具，不是公网认证或公网部署方案。
- 默认 Mock 不产生业务 Finding；在线 LLM 需要用户自行完成安全配置、脱敏和组织审批。
- PDF、OCR、RAG、知识图谱、复杂历史前端和正式 42 章规则库仍未实现。
- Windows 无 symlink 权限时，本地无法亲自执行 symlink 分支；应在 CI 或具备该权限的环境补跑。
- DOCX 解析、规则加载、在线 Provider 和数据库迁移仍需按部署环境单独做运维级验证。
