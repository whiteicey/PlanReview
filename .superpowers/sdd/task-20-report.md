# Task 20 Report

## Status

Completed. Implemented the full local FastAPI contract and a static local UI.

The API provides health and safe configuration, UUID4-isolated DOCX case uploads, size and file-type validation, durable review execution through `DocxParser`, `ReviewPipeline`, `MockProvider`, and `ReviewRepository`, persisted findings, case-scoped expert review updates, XLSX/DOCX/anonymous exports, recycle-bin confirmation, and case-bound permanent deletion.

The application reads its local launch host from `get_settings()` and defaults to `127.0.0.1`. Manual local startup is documented in `app/main.py`:

```text
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## Commits

- Pending at report creation.

## Tests and output

Initial TDD confirmation:

```text
python -m pytest tests/contract/test_api.py tests/contract/test_api_acceptance_path.py tests/security/test_api_local_only.py -v
1 collection error: ModuleNotFoundError: No module named 'app.main'
```

Focused API contract verification:

```text
python -m pytest tests/contract/test_api.py tests/contract/test_api_acceptance_path.py tests/security/test_api_local_only.py -v
11 passed, 4 warnings in 1.99s
```

Full regression verification:

```text
python -m pytest -q
235 passed, 4 warnings in 6.75s
```

Whitespace verification:

```text
git diff --check
exit code 0
```

The four warnings are pre-existing Pytest configuration and FastAPI/Starlette deprecated HTTP status aliases; no test failed.

## Concerns

- PDF and OCR remain intentionally unsupported. Non-DOCX uploads receive HTTP 415 with the text-only DOCX message.
- Export generation contains only persisted review artifacts. The anonymous ZIP intentionally excludes case IDs, source document files, absolute paths, provider details, request IDs, and credentials.
- Rules are currently supplied as an empty local set for the API review path; the deterministic mock provider and parser still create evidence-backed review findings when its configured condition appears.
