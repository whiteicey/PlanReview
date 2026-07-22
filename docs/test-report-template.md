# 测试报告模板（如实填写，禁止虚构指标）

> 使用方法：复制为 `docs/test-report-<日期>.md`，按实际命令输出填写。空缺处标「未执行」或
> 「未测量」，不得填入未实测的数值。

## 环境

- 日期：<YYYY-MM-DD>
- Python 版本：<例如 3.12.10>
- 操作系统：<例如 Windows 11>
- 示例数据包：<REVIEW_DEMO_ROOT 路径，或「未提供」>

## 测试命令与结果

| 命令 | passed | failed | skipped | 备注 |
|------|--------|--------|---------|------|
| `python -m pytest -q` | | | | |
| `REVIEW_DEMO_ROOT=... python -m pytest -q tests/golden` | | | | 未提供示例包时应显示 SKIPPED |

失败堆栈：<链接或粘贴，若无写「无」>

## 金标准回归（如实）

| 案例 | 期望 | 实测 | 一致？ |
|------|------|------|--------|
| G-001 基线 0 findings | 0 | | |
| G-002 | | | |
| G-003 | | | |
| G-004 | | | |
| G-005/006 反向不误报 | UNKNOWN，不 FAIL | | |
| G-007 单位换算 | PASS | | |
| G-008 证据不足 | UNKNOWN + blocked | | |

## 未执行范围（必须列出）

- PDF 文本层解析：未执行（本次不实现）
- 扫描页 OCR / 视觉模型：未执行（本次不实现）
- 真实在线 LLM（Anthropic/OpenAI）：未执行（本次使用确定性 Mock）
- 向量 RAG 检索：未执行（本次关键词占位）
- 与 AI 中台盲评：未执行

## 人工复核样本

<如做过专家复核，记录样本数与确认/驳回/修改分布；未做写「未执行」>

## 禁止事项自检

- [ ] 未使用未实测的「准确率」「召回率」「节省时间」「成本」等宣传数值
- [ ] 未把 SKIPPED 报告为 PASS
- [ ] 未把 UNKNOWN/BLOCK/缺证据/解析失败静默转为 PASS
- [ ] 所有导出与页面保留「AI 初审结果，不是正式审查结论」
## 收口验收填写要求

报告必须单独记录 LLM 状态（`NOT_RUN`、`COMPLETED`、`COMPLETED_PARTIAL`、`CONFIGURATION_ERROR`、`PROVIDER_ERROR`、`INPUT_LIMIT_EXCEEDED`、`VALIDATION_FAILED`）、规则 Finding 是否保留、symlink 测试是否因 Windows 权限跳过，以及是否执行了 golden。不得把 AI 初审写成正式结论，也不得把 PDF/OCR/RAG/知识图谱或公网部署写成已支持能力。
