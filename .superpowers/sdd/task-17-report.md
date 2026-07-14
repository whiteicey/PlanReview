# Task 17 Report

## Status

Completed. Added real SQLAlchemy 2.x SQLite ORM persistence for `cases`, `case_files`, `review_runs`, `rule_results`, `findings`, and `recycle_bin`. Repository writes commit and refresh; fresh sessions hydrate active runs from SQLite. Case metadata accepts only SHA-256 file metadata and normalized POSIX-relative storage paths. Findings support durable expert-review updates. Cases move to a database-backed recycle bin and require the exact `DELETE {case_id}` confirmation before permanent deletion.

Final review hardening enforces persistence-wide content safety. Finding title/description/suggestion, RuleResult message/details, human notes, facts/stage JSON, case statistics, and nested/list JSON strings are checked with a conservative bounded-content policy. API-key/token/authorization patterns, AWS/GitHub tokens, request/response payload markers, raw DOCX/document-content markers, oversized/multiline text, and large JSON payloads fail closed. Sensitive JSON keys remain filtered before persistence. `save_run` explicitly deletes and flushes old child rows before reinserting, so same-finding-ID reruns are safe.

## Commits

- `53cc988 feat: persist review runs without secrets and support human review`
- `9d00e2b docs: record task 17 persistence verification`
- `2936ac0 fix: harden persistence review notes and reruns`
- `08a0c16 docs: record task 17 review hardening`
- Final review follow-up pending commit.

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

Review hardening focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -q
13 passed, 1 warning in 2.23s
```

Final review focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -q
20 passed, 1 warning in 2.99s
```

Final full regression verification:

```text
python -m pytest -q
169 passed, 1 warning in 3.71s
```

The remaining warning is the existing `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Changed files in final review follow-up

- `app/persistence/repository.py`
- `tests/security/test_no_secrets_in_persistence.py`
- `.superpowers/sdd/task-17-report.md`

## Concerns

- Prohibited content is rejected rather than silently partially redacted, so callers must submit short safe review summaries.
- Conservative content checks can reject legitimate text containing reserved security/document markers; this is intentional fail-closed behavior.
- The repository never writes source document bytes or complete external request bodies. The SQLite schema is created at session-factory startup; schema migrations are outside this task.
