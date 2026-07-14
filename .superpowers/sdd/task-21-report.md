# Task 21 Report

## Status

Completed. Implemented Excel, Word, and strict anonymous finding exports with the required disclaimer, evidence references, persisted expert review states, anonymized rule-version records, evidence-text SHA-256 hashes, and honest unmeasured metrics.

## Changes

- Excel and Word exports include the disclaimer, evidence span IDs, review status, and expert notes. Re-exporting a run reads the current persisted review state.
- Anonymous ZIP uses a one-file allow-list (`anonymous-findings.json`) and does not serialize case identity, raw span IDs, source text/documents, paths, provider/system credentials, request metadata, or URLs.
- Anonymous findings and rule IDs are replaced with opaque sequential aliases. Prose containing identifiers, prohibited metadata markers, URLs, paths, or document extensions is redacted.
- Pipeline retains only `SourceSpan.text_hash` values for later anonymous export; raw evidence text is not retained.
- Rule versions and evidence text hashes are persisted in the local review schema so reviewed runs can be exported after restart.

## Commits

- Pending commit: `feat: export findings with disclaimer and anonymous package guard`

## Exact tests/output

Initial TDD confirmation:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py -v
4 failed
```

Focused exporters:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py -v
4 passed, 1 warning in 0.76s
```

Task 20 integration regression:

```text
python -m pytest tests/unit/test_exporters.py tests/security/test_anonymous_export.py tests/unit/test_repository.py tests/unit/test_review_pipeline.py tests/contract/test_api.py -v
36 passed, 1 warning in 4.65s
```

Full suite:

```text
python -m pytest -v
241 passed, 1 warning in 7.50s
```

The warning is pre-existing pytest configuration: `PytestConfigWarning: Unknown config option: asyncio_mode`.

## Concerns

- Existing SQLite databases created before this task do not receive automatic migrations. New databases created through the current session factory have the new rule-version and evidence-hash columns. Legacy in-memory/manual runs without retained evidence hashes export anonymous findings with no hash entry rather than fabricating a hash.
