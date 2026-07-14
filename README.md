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
