# Review Assistant

开发方案审查助手（本地版）内核，面向文本型 DOCX 形式初审。

## 开发

项目要求 Python 3.12。安装开发依赖后运行测试：

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

应用默认仅绑定本机地址，文件存储位于 `storage/`。AI 初审结果，不是正式审查结论。
