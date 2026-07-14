# Task 22 report

## Status

Implemented and verified. Added external DEMO rule/terminology import, loopback-only local startup, contract tests, and DEMO/README instructions. Source DOCX files remain outside the repository and are never copied into `storage/` or Git.

## Commit

- `docs: add local demo workflow and loopback startup` (this commit)

## Tests and exact output

- `python -m pytest tests/contract/test_demo_import.py -v`
  - `5 passed, 1 warning in 0.06s`
- `python -m pytest -q`
  - `248 passed, 1 warning in 7.48s`
- CLI smoke test with `REVIEW_DEMO_ROOT` and external `DEMO-001_正常基线方案_V1.0.docx`
  - exit code `0`
  - JSON reported `source_type: DEMO_ONLY` and `copied_to_storage: false`

## Concerns

- Pytest reports the pre-existing warning `Unknown config option: asyncio_mode`; it does not fail the suite.
- PDF/OCR is explicitly deferred; only text DOCX is supported by the demo workflow.
