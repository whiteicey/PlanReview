# Task 21 Report

## Status

Completed. Implemented Excel, Word, and strict anonymous finding exports with the required disclaimer, evidence references, persisted expert review states, anonymized rule-version records, evidence-text SHA-256 hashes, and honest unmeasured metrics.

## Changes

- Excel and Word exports include the disclaimer, evidence span IDs, review status, and expert notes. Re-exporting a run reads the current persisted review state.
- Anonymous ZIP uses a one-file allow-list (`anonymous-findings.json`) and does not serialize case identity, raw span IDs, source text/documents, paths, provider/system credentials, request metadata, or URLs.
- Anonymous findings and rule IDs are replaced with opaque sequential aliases. Anonymous exports use an allow-list and omit arbitrary title, description, suggestion, and human-note prose entirely; ordinary-looking source-body text is therefore not exported.
- Pipeline retains only `SourceSpan.text_hash` values for later anonymous export; raw evidence text is not retained.
- Rule versions and evidence text hashes are persisted in the local review schema so reviewed runs can be exported after restart.

## Commits

- `481851d feat: export findings with disclaimer and anonymous package guard`
- `5381898 fix: migrate export metadata columns`
- `PENDING` follow-up superseded by fail-closed anonymous prose hardening in the final commit below.

## Exact tests/output

Initial TDD confirmation:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py -v
4 failed
```

Adversarial hardening focused exporters:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py -v
4 passed, 1 warning in 0.72s
```

Migration and integration regression:

```text
python -m pytest tests/unit/test_db_migrations.py tests/unit/test_exporters.py tests/security/test_anonymous_export.py tests/unit/test_repository.py tests/contract/test_api.py -v
36 passed, 1 warning in 4.76s
```

Task 20 integration regression:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py tests/unit/test_repository.py tests/unit/test_review_pipeline.py tests/contract/test_api.py -v
36 passed, 1 warning in 4.65s
```

Full suite:

```text
python -m pytest -v
242 passed, 1 warning in 7.28s
```

The warning is pre-existing pytest configuration: `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Concerns

- Existing SQLite databases receive additive migration for the new rule-version and evidence-hash columns at session creation. Legacy in-memory/manual runs without retained evidence hashes export anonymous findings with no hash entry rather than fabricating a hash.
