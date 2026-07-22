# PlanReview V1.2 横版UI自动验收报告

## 结论

**PASS**。横版严格采用用户提供的浅色宽导航、案件栏、中央执行台和右侧摘要板式；紧凑版与简化版使用同一业务DOM和状态链路。

## 自动测试

- 迁移前：578 passed，3 skipped；581个测试ID。
- 迁移后：582 passed，3 skipped；585个测试ID；exit code 0。
- 原有测试ID减少：0。
- 新增pytest测试ID：4。
- 新增xfail：0。
- 前端Node测试：40 passed，0 failed。
- 所有新增/修改JavaScript通过`node --check`。
- `git diff --check`通过。

## 真实Run只读回放

- 回放端口：`8890`。
- 回放数据：仓库外临时只读目录，未复制Provider配置。
- 浏览器请求：30次，全部为GET。
- 业务POST/PUT/PATCH/DELETE：0。
- Provider请求：0。
- 浏览器控制台错误：0。
- 服务端新增traceback：0。
- Run数量和状态在回放前后不变。
- final_status：`READY_FOR_HUMAN_REVIEW`。
- llm_status：`COMPLETED_PARTIAL`。
- 页面/API/隔离数据库/XLSX Finding：均为25条。
- RuleResult：29条；distinct rule_id：18个。
- AI batch：5批，顺序保持，stop_reason均为`end_turn`。
- packet ledger：504条、531983字节、未截断。
- AI candidate ledger：119条、70778字节、未截断。
- stage records：数据库中8项均为completed；UI因summary不含逐项状态，仅显示“阶段记录8项”。

## UI语义

- `llm_finding_count=40`按生产语义显示为“AI有效候选”。
- 最终规则/混合/AI数量由25条最终Finding的`origin`派生。
- 缺失diagnostics字段隐藏；null不会被误显示为0。
- READY不用于推断全部阶段completed。

## 视觉验收

已生成并人工查看以下脱敏全页截图：

- 1440×900
- 1280×800
- 1024×768
- 768×1024
- 390×844

真实Run截图已遮罩案例号、文件名、Run ID、Finding标题/正文和执行事件正文。

## 文件边界

生产修改仅涉及：

- `web/index.html`
- `web/styles.css`
- `web/app.js`
- `web/review_progress.js`
- `web/layout.js`
- `web/workbench_state.js`

其余新增文件均为前端单测、E2E/只读核验脚本、脱敏样本、截图和验收文档。

## 已知限制

- 当前Run summary只返回阶段名称列表，不返回逐项状态；UI不会越权推断全部完成。
- 真实回放原始副本仍保留在仓库外临时目录，供人工复核后清理；未进入Git或交付ZIP。
- 本次不打Tag、不发布、不推送远程。
