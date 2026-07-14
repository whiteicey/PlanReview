# Task 17 Report

## Status

Completed. Added real SQLAlchemy 2.x SQLite ORM persistence for `cases`, `case_files`, `review_runs`, `rule_results`, `findings`, and `recycle_bin`. Repository writes commit and refresh; fresh sessions hydrate active runs from SQLite. Case metadata accepts only SHA-256 file metadata and normalized POSIX-relative storage paths. Findings support durable expert-review updates. Cases move to a database-backed recycle bin and require the exact `DELETE {case_id}` confirmation before permanent deletion.

Final safety and transaction hardening validates every persisted identifier/list field (`finding_id`, category, parameter, rule ID, evidence IDs, fact IDs) against a bounded ASCII identifier allowlist. Content checks cover API keys, generic tokens, authorization/Bearer values, AWS keys, GitHub tokens including `github_pat`, Google `AIza` keys, JWTs, PEM private keys, request/response payload markers, raw document markers, and bounded JSON/prose. `save_run` preflights the entire payload and rolls back on commit/validation errors, preventing partial replacement rows. Finding IDs are unique per review run, allowing the same local ID in separate cases. Recycled cases reject `save_run`; no implicit restore exists.

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

Final review focused verification:

```text
python -m pytest tests/unit/test_repository.py tests/security/test_no_secrets_in_persistence.py -q
27 passed, 1 warning in 3.74s
```

Final full regression verification:

```text
python -m pytest -q
176 passed, 1 warning in 4.49s
```

The remaining warning is the existing `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Changed files in final review follow-up

- `app/persistence/models.py`
- `app/persistence/repository.py`
- `tests/unit/test_repository.py`
- `.superpowers/sdd/task-17-report.md`

## Concerns

- Prohibited content and identifiers are rejected rather than silently partially redacted, so callers must submit short safe summaries and opaque IDs.
- Conservative checks can reject legitimate text containing reserved security/document markers; this is intentional fail-closed behavior.
- The SQLite schema is created at session-factory startup; schema migrations are outside this task.
