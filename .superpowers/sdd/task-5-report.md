# Task 5 Report

Status: COMPLETE

## Commit

- `78f2a4312ef033a223e6ea8ad3e8217d471d477d` — `feat: path-traversal guard case isolation and file hashing`

## Delivered

- Added `app.storage.paths.safe_join`, which resolves paths below a trusted root and rejects empty/current/parent segments, native and Windows absolute paths, drive-qualified paths, and resolved escapes.
- Added `validate_upload_name`, which strips client-supplied paths, rejects invalid names, and allows only configured extensions case-insensitively.
- Added lowercase hexadecimal SHA-256 helpers for bytes and UTF-8 text.
- Added immutable `StoredFile` metadata and `store_upload`, which accepts only canonical UUID4 case IDs, stores uploads below `cases/<uuid4>/documents`, records storage-relative POSIX paths only, names files `<sha256>-<safe_name>`, and uses exclusive creation so existing bytes cannot be overwritten.
- Added TDD coverage for traversal, Windows drive syntax, upload-name validation, SHA-256 vectors, UUID4 isolation, relative metadata, and overwrite protection.

## Tests run

1. `python -m pytest tests/security/test_paths.py tests/unit/test_hashing.py -v` (before implementation)
   - Result: expected failure during collection.
   - Output: `ModuleNotFoundError: No module named 'app.storage'` for both test modules.
   - Summary: `2 errors`, exit code `2`.

2. `python -m pytest tests/security/test_paths.py tests/unit/test_hashing.py -v` (after paths/hashing implementation)
   - Result: passed.
   - Output: `10 passed, 1 warning in 0.05s`.

3. `python -m pytest tests/security tests/unit/test_hashing.py -v` (after case-files implementation)
   - Result: passed.
   - Output: `13 passed, 1 warning in 0.06s`.

4. `python -m pytest -v`
   - Result: passed.
   - Output: `26 passed, 1 warning in 0.15s`.

5. `git diff --check`
   - Result: passed with no whitespace errors.

## Concerns

- Pytest emits `PytestConfigWarning: Unknown config option: asyncio_mode` because this environment lacks the optional `pytest-asyncio` plugin. The application changes are unaffected.
- The repository ignores any directory named `storage/`; source files under `app/storage/` were force-added intentionally. This ignore rule will require the same care for future source files in that package.
- Duplicate upload attempts in the same UUID4 case with identical contents and names intentionally raise `FileExistsError`; the existing file is never modified. API-level behavior for surfacing that conflict is outside Task 5.
