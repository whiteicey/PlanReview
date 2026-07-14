# 演示操作

本应用仅处理文本型 DOCX；PDF/OCR 当前不支持，用户提出后再规划。

> AI 初审结果，不是正式审查结论

规则、术语、标准与 golden 均为 `DEMO_ONLY` 虚构演示数据，不构成正式审查依据。

## 边界与数据位置

示例数据包位于仓库外的 `本地版示例数据包/`。导入脚本使用 `REVIEW_DEMO_ROOT` 时必须指向该目录；未设置时只按脚本约定的两个候选路径查找，不生成或伪造文档。规则和术语来自 `rules/` 下的 `ruleset-demo-0.1.yaml` 与 `terminology-demo-0.1.yaml`，并要求 `source_type: DEMO_ONLY`。

待上传的示例 DOCX 必须由用户提供外部路径。`scripts/import_demo.py` 只读取规则/术语并记录 DOCX 路径，绝不把源文件复制到 `storage/` 或提交到 Git；应用实际上传后产生的案例存储仍受本地存储和删除流程约束。

## 操作步骤

1. 启动：`python scripts/run_local.py`。服务只绑定 `127.0.0.1:8765`，不接受改成公网监听地址的参数。
2. 浏览器打开 `http://127.0.0.1:8765`。
3. 设置示例包位置（如未落在默认候选目录）：`set REVIEW_DEMO_ROOT=C:\path\to\本地版示例数据包`（PowerShell 使用 `$env:REVIEW_DEMO_ROOT=...`）。
4. 使用 `python scripts/import_demo.py "C:\path\to\DEMO-001.docx"` 校验外部 DOCX 并读取示例规则、术语；只支持 `.docx`，缺少文件或使用 PDF 会明确报错。
5. 在页面上传 DEMO DOCX，查看章节、参数事实、规则结果及证据 span。
6. 对 UNKNOWN/FAIL Finding 进行专家确认、驳回、修改或补充备注。证据链用于把问题定位回文档的章节、段落或表格 span，不等同于正式技术结论。
7. 导出 Excel/Word/匿名包；匿名包不含厂商、模型、Base URL、Request ID、key 或源文件。
8. 删除案例先进入回收站，再二次确认永久删除。

示例规则、术语、标准、历史意见和 golden 仅供系统开发测试。拿到真实资料后应建立独立知识集，经专家确认后再发布，不能把 `DEMO_ONLY` 与正式规范混用。
