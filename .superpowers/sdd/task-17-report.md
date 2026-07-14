# Task 17 Report

## Status

Completed. Added real SQLAlchemy 2.x SQLite ORM persistence for `cases`, `case_files`, `review_runs`, `rule_results`, `findings`, and `recycle_bin`. Repository writes commit and refresh; fresh sessions hydrate active runs from SQLite. Case metadata accepts only SHA-256 file metadata and normalized POSIX-relative storage paths. Findings support durable expert-review updates. Cases move to a database-backed recycle bin and require the exact `DELETE {case_id}` confirmation before permanent deletion.

The review hardening follow-up now fails closed for expert notes containing API keys, token/authorization values, request/response bodies, document content, or body-shaped oversized/multiline text. Recursive JSON filtering also removes sensitive/body-bearing keys. `save_run` explicitly bulk-deletes and flushes old child rows before inserting replacements, so reruns with the same finding IDs are safe.

## Commits

- `53cc988 feat: persist review runs without secrets and support human review`
- `9d00e2b docs: record task 17 persistence verification`
- Follow-up fixes pending commit.

## Tests and output

Initial TDD confirmation before implementation:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -v
2 collection errors: ModuleNotFoundError: No module named 'app.persistence'
```

Pre-review focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -v
6 passed, 1 warning in 1.44s
```

Review follow-up focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -q
13 passed, 1 warning in 2.23s
```

Full regression verification after follow-up:

```text
python -m pytest -q
162 passed, 1 warning in 2.97s
```

The remaining warning is the existing `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Changed files in review follow-up

- `app/persistence/repository.py`
- `tests/unit/test_repository.py`
- `tests/security/test_no_secrets_in_persistence.py`
- `.superpowers/sdd/task-17-report.md`

## Concerns

- Expert notes reject prohibited content rather than silently storing or partially redacting it; callers must submit a short safe summary.
- The repository never writes source document bytes or complete external request bodies; safe finding descriptions remain persisted because they are review records, not raw DOCX or request payloads.
- The SQLite schema is created at session-factory startup for this local application. Schema migrations are not included in this task.
