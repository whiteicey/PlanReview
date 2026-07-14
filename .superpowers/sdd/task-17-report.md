# Task 17 Report

## Status

Completed. Added real SQLAlchemy 2.x SQLite ORM persistence for `cases`, `case_files`, `review_runs`, `rule_results`, `findings`, and `recycle_bin`. Repository writes commit and refresh; fresh sessions hydrate active runs from SQLite. Case metadata accepts only SHA-256 file metadata and relative storage paths. Findings support durable expert-review updates. Cases move to a database-backed recycle bin and require the exact `DELETE {case_id}` confirmation before permanent deletion.

Persistence is intentionally metadata-only: no credential fields, raw DOCX bytes/bodies, or full request/response bodies are schema columns. JSON metadata recursively drops sensitive/body-bearing keys.

## Commits

- `53cc988 feat: persist review runs without secrets and support human review`

## Tests and output

Initial TDD confirmation before implementation:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -v
2 collection errors: ModuleNotFoundError: No module named 'app.persistence'
```

Focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -v
6 passed, 1 warning in 1.44s
```

Full regression verification:

```text
python -m pytest -q
155 passed, 1 warning in 2.17s
```

The warning is the existing `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Concerns

- The repository never writes source document bytes or complete external request bodies; safe fields such as finding descriptions remain persisted because they are review records, not raw DOCX or request payloads.
- The SQLite schema is created at session-factory startup for this local application. Schema migrations are not included in this task.
