# Task 20 Report

## Status

Completed. Implemented the full local FastAPI contract and a static local UI.

The API provides health and safe configuration, UUID4-isolated DOCX case uploads, size and file-type validation, durable review execution through `DocxParser`, `ReviewPipeline`, `MockProvider`, and `ReviewRepository`, persisted findings, case-scoped expert review updates, XLSX/DOCX/anonymous exports, recycle-bin confirmation, and case-bound permanent deletion.

The application reads its local launch host from `get_settings()` and defaults to `127.0.0.1`. Manual local startup is documented in `app/main.py`:

```text
uvicorn app.main:app --host 127.0.0.1 --port 8765
```

## Commits

- `34b11b1 feat: add local FastAPI review workflow`
- `b19ac5e fix: harden API uploads exports and deletion`
- `412a9e7 docs: record Task 20 review fixes`

## Tests and output

Initial TDD confirmation:

```text
python -m pytest tests/contract/test_api.py tests/contract/test_api_acceptance_path.py tests/security/test_api_local_only.py -v
1 collection error: ModuleNotFoundError: No module named 'app.main'
```

Focused API contract verification before review fixes:

```text
python -m pytest tests/contract/test_api.py tests/contract/test_api_acceptance_path.py tests/security/test_api_local_only.py -v
11 passed, 4 warnings in 1.99s
```

Focused API/security verification after review fixes:

```text
python -m pytest tests/contract/test_api.py tests/contract/test_api_acceptance_path.py tests/security/test_api_local_only.py -q
13 passed, 1 warning in 2.19s
```

Full regression verification after review fixes:

```text
python -m pytest -q
237 passed, 1 warning in 7.29s
```

Whitespace verification:

```text
git diff --check
exit code 0
```

The remaining warning is the pre-existing `PytestConfigWarning: Unknown config option: asyncio_mode`; no test failed. Deprecated FastAPI status aliases were replaced.

Review fixes include opaque evidence IDs in anonymous exports, exact-confirmation deletion of case-scoped documents and report artifacts, chunked upload enforcement, real multipart over-limit coverage, DOCX ZIP/package validation at upload, and clear 415 responses for renamed PDFs/non-DOCX packages.

## Concerns

- PDF and OCR remain intentionally unsupported. Non-DOCX uploads receive HTTP 415 with the text-only DOCX message.
- Export generation contains only persisted review artifacts. The anonymous ZIP intentionally excludes case IDs, source document files, absolute paths, provider details, request IDs, and credentials.
- Rules are currently supplied as an empty local set for the API review path; the deterministic mock provider and parser still create evidence-backed review findings when its configured condition appears.
