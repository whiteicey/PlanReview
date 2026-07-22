# PlanReview 返修与发布包验收报告

验收日期：2026-07-18

目标环境：Windows，Python 3.12.13

起始提交：`143367e`

Release source commit：`d2b81d6f3438923605760c0aec4229c1c2c71811`

## 修复提交

| 阶段 | 提交 | 内容 |
|---|---|---|
| R0 | `dfa4528` | 从 Git tree 构建发布 ZIP，并验证完整清单与禁止项 |
| R1 | `1bd3529` | 不完整同名 Fact 返回 UNKNOWN；精确去重保留 merged fact/span 证据链 |
| R2 | `d3d1285` | 配置缺失或损坏时禁止复用孤儿 API Key |
| R3 | `55a37c8` | 规则未加载且 AI 未完成时不显示空成功、导出或专家复核 |
| R4 | `be3afd0` | Finding 标题/正文边界统一，并缩窄敏感文本误判 |
| R5 | `5131400` | 修复 Windows symlink 测试的异常类型作用域 |
| R6 | `d120d7f` | 文档、12 条规则说明与随包 DEMO 布局对齐 |
| 发布复验 | `d2b81d6` | 解压目录允许本地 venv/cache/runtime 产物，ZIP 仍严格拒绝这些成员 |

## 发布 ZIP

- 文件名：`PlanReview_repaired_d2b81d6.zip`
- 构建来源：`d2b81d6f3438923605760c0aec4229c1c2c71811`
- SHA-256：`DFDDDA99FF111A40C7348589462BF6DCC1C0634BB9DFA10EB5121FD139B26645`
- 清单验证：185 个文件成员，26 个必需文件，禁止项为零。
- 验收顺序：从 release source commit 生成 ZIP；验证 ZIP；解压到全新目录；创建项目外 Python 3.12 venv；安装 `.[dev]`；执行全部测试和应用烟测。

## 全新解压目录自动测试

验收目录：`PlanReview_release_acceptance_d2b81d6`。测试使用独立 venv，未引用原工作树。

| 测试组 | 实际结果 |
|---|---|
| Unit | 290 passed, 3 skipped, 1 warning；25.75s |
| Contract | 71 passed, 1 warning；17.21s |
| Security | 99 passed；7.80s |
| Golden | 16 passed；2.65s |
| Full | 476 passed, 3 skipped, 1 warning；42.79s |
| Node | 11 passed, 0 failed；163.7627ms |

Golden 期望文件未修改。Full/Unit 的三个 skip 均来自 Windows 当前环境无法创建符号链接：

- storage 子目录 symlink 拒绝测试；
- storage 根父目录 symlink 拒绝测试；
- case 树内文件 symlink 拒绝测试。

定向 symlink 文件结果为 4 passed、3 skipped；三项均明确报告 `symbolic links are unavailable in this Windows environment`。测试代码作用域守卫已实际执行并通过，真正创建 symlink 的三个分支因操作系统权限未执行。

## 应用烟测

在全新解压目录使用独立 venv 启动 `python scripts/run_local.py`，结果：

- 首页：HTTP 200，包含“方案审查助手”。
- 规则状态：HTTP 200，`loaded=true`，共 12 条规则。
- DEMO 上传：附带的 DEMO-001，HTTP 201。
- Mock 审查：HTTP 201，Run 为 `READY_FOR_HUMAN_REVIEW`；`llm_provider=mock`、`llm_status=COMPLETED`、`llm_finding_count=0`。
- Run 查询：列表及指定 Run 均 HTTP 200，run_id 一致。
- 成功导出：XLSX HTTP 200，返回有效 ZIP/XLSX 文件头。
- 失败 Run 导出保护：仅含 FAILED Run 的独立案例导出返回 HTTP 409。

## 验收过程说明与剩余风险

- 源工作树第一次全量复跑曾出现一次 Windows 并发数据库测试的瞬时路径异常；该测试单独复跑通过，随后源工作树全量复跑为 475 passed、3 skipped。最终 ZIP 的全新环境 Full 为 476 passed、3 skipped。
- 第一次干净目录尝试把 venv/basetemp 放在源码树内，暴露并修复了解压目录验证器对本地产物的误报；最终 ZIP 已从修复后的新 release source commit 重建并重新完整验收。
- 在线 LLM 未使用真实凭据或外部服务调用；Mock 调用链和配置失败边界由自动测试覆盖。
- 首页烟测验证了真实本地服务和 HTML 内容，未执行人工浏览器像素级视觉检查。
- Windows 当前权限不支持真实创建 symlink，因此三个安全分支仍需在具备 symlink 权限的 Windows runner 再执行一次。
- 未实现或重复重构版本 Diff、PDF、OCR、RAG、知识图谱以及复杂历史前端。
- 未 push，未创建或合并 PR。
