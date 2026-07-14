# Task 17 Report

## Status

Completed. Added real SQLAlchemy 2.x SQLite ORM persistence for `cases`, `case_files`, `review_runs`, `rule_results`, `findings`, and `recycle_bin`. Repository writes commit and refresh; fresh sessions hydrate facts, rule results, findings, stages, and final status directly from SQLite. Field-aware facts preserve safe Unicode `source_document` metadata and validate fact/span identifiers during serialization and fresh-session hydration. Case metadata accepts only SHA-256 file metadata, bounded case/file identifiers, portable Unicode `.docx` basename names (no separators/traversal/roots, max 255), and normalized POSIX-relative storage paths. Findings support case-scoped expert-review updates. Cases move to a database-backed recycle bin; `save_case` and `save_run` reject recycled cases and no implicit restore exists.

All mutating repository methods use rollback guards around ORM mutation, flush, and commit. The intentionally scoped review interface is `update_finding_review(case_id, finding_id, status, note)`; the prior unscoped local-ID form is not retained because it permits ambiguous cross-case updates. Reuse-after-failure coverage confirms sessions remain usable. Content checks cover API keys, generic tokens, authorization/Bearer values, AWS keys, GitHub tokens including `github_pat`, Google `AIza` keys, embedded JWTs, PEM private keys, request/response payload markers, raw document markers, and bounded JSON/prose.

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
38 passed, 1 warning in 4.80s
```

Final full regression verification:

```text
python -m pytest -q
187 passed, 1 warning in 5.49s
```

The remaining warning is the existing `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Changed files in final review follow-up

- `app/persistence/repository.py`
- `tests/unit/test_repository.py`
- `.superpowers/sdd/task-17-report.md`

## Concerns

- Prohibited content and identifiers are rejected rather than silently partially redacted, so callers must submit short safe summaries and opaque IDs.
- Conservative checks can reject legitimate text containing reserved security/document markers; this is intentional fail-closed behavior.
- The SQLite schema is created at session-factory startup; schema migrations are outside this task.
