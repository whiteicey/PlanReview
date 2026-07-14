# Task 22 report

## Status

Review correction implemented. The contract fixture now skips honestly when the external demo package is absent. The importer accepts the established top-level `rules`/`aliases` schemas, validates rules and terminology through production loaders, enforces per-rule `DEMO_ONLY`, and never copies DOCX files into storage.

## Commit

- Previous implementation: `13a0fcf073ad37f9db2ce6535f1e44783d0c362e`
- Review correction: `fix: validate demo imports through production loaders` (this commit)

## Tests and exact output

- `python -m pytest tests/contract/test_demo_import.py -v`
  - `5 passed, 2 skipped, 1 warning in 0.15s`
  - Skips: external DEMO package unavailable in the worktree fixture environment; skips are explicit and have a clear reason.
- `python -m pytest -q`
  - `248 passed, 2 skipped, 1 warning in 7.45s`
- Valid external-package CLI smoke test with `REVIEW_DEMO_ROOT` and external `DEMO-001_正常基线方案_V1.0.docx`
  - exit code `0`
  - JSON reported `rule_count: 10`, `source_type: DEMO_ONLY`, and `copied_to_storage: false`

## Concerns

- Pytest reports the pre-existing warning `Unknown config option: asyncio_mode`; it does not fail the suite.
- PDF/OCR is explicitly deferred; only text DOCX is supported by the demo workflow.
- The external generated package contains legacy `on_missing: suspected`; the importer preserves that intent in rule params while normalizing it to production `unknown` for loader validation.
