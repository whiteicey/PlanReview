# 开发方案审查助手（本地版）

面向文本型 DOCX 形式初审的本地内核。AI 初审结果，不是正式审查结论。

## 开发

项目要求 Python 3.12。安装开发依赖后运行测试：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

应用默认仅绑定 `127.0.0.1:8765`，文件存储位于 `storage/`。完整演示流程见 [`docs/DEMO.md`](docs/DEMO.md)。

## 本地 DEMO

示例规则、术语、标准和 golden 都是 `DEMO_ONLY` 虚构演示数据，位于仓库外的 `本地版示例数据包/`，不构成正式审查依据。用 `REVIEW_DEMO_ROOT` 显式指定示例包位置，然后运行：

```bash
python scripts/import_demo.py "C:\\path\\to\\sample.docx"
python scripts/run_local.py
```

导入脚本只读取规则/术语并引用外部 DOCX，不复制源文件到 `storage/` 或 Git。当前仅处理文本型 DOCX，PDF/OCR 当前不支持。

## 验收

运行：

```bash
python -m pytest -q
```

外部 DEMO 金标准回归需显式提供示例包：

```bash
REVIEW_DEMO_ROOT="C:\\path\\to\\本地版示例数据包" python -m pytest -q tests/golden
```

验收报告必须如实列出 PASS、FAIL、SKIPPED、未跑指标及原因（模板见
[`docs/test-report-template.md`](docs/test-report-template.md)）。禁止用「准确率」「召回率」
「节省时间」「成本」等未实测数值作宣传；未执行的 PDF/OCR、真实在线 LLM、向量检索必须标注
「未执行」。所有页面、Excel、Word、匿名包都保留「AI 初审结果，不是正式审查结论」。任何
UNKNOWN、BLOCK、缺证据、解析失败、外部 LLM 失败都不得静默变成 PASS。