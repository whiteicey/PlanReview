# Task 18 Report

## Status

Completed and hardened against TOCTOU and constructor backend injection. `CredentialStore` has only the public `service` constructor argument; production always compares against the genuine imported `keyring.backends.Windows.WinVaultKeyring` through a module-private expected-type seam. Ordinary callers cannot select an alternate expected backend type. Tests may monkeypatch that private module seam for controlled doubles without exposing an injection API. After exact type validation, operations invoke set/get/delete on that same validated backend instance, never top-level keyring helpers, so an active/top-level backend swap cannot redirect an operation. Unsafe or unusable backends fail closed before receiving provider or key data. There is no local caching or plaintext persistence. Provider/service names are bounded safe identifiers, and all keyring failures expose only the provider name. Missing credentials can be deleted idempotently.

## Commits

- `c62cf2f feat: store provider keys only in Windows Credential Manager`
- `1959f98 fix: require Windows Credential Manager backend`
- `5af0ef4 docs: record credential backend hardening`
- `81c1606 fix: validate genuine Windows keyring backend`
- `cf8acbe docs: record exact backend identity verification`
- `e8c7ed3 fix: use validated keyring backend instance`
- `62b86f0 docs: record keyring TOCTOU hardening`
- Follow-up private-seam fix commit pending.

## Tests and output

Initial TDD confirmation:

```text
python -m pytest tests/security/test_credentials.py -v
1 collection error: ModuleNotFoundError: No module named 'keyring'
```

Final focused verification:

```text
python -m pytest tests/security/test_credentials.py -v
10 passed, 1 warning in 0.17s
```

Final full regression verification:

```text
python -m pytest -q
197 passed, 1 warning in 5.43s
```

The warning is the existing configuration warning:

```text
PytestConfigWarning: Unknown config option: asyncio_mode
```

## Changed files

- `app/security/__init__.py`
- `app/security/credentials.py`
- `tests/security/test_credentials.py`
- `.superpowers/sdd/task-18-report.md`

## Concerns

- Backend validation is exact and fail-closed. Alternate backends, wrappers, proxies, spoof classes, unusable backends, or non-Windows platforms are rejected rather than risking plaintext or unsafe persistence.
- The public constructor exposes no backend type/factory injection. The private module seam is used only by tests for controlled doubles; production defaults to and compares the genuine WinVault class.
- The ordinary-constructor regression confirms attempts to pass `_expected_backend_type` fail, while the default constructor still rejects unsafe active backends.
- The TOCTOU regression confirms swapping `keyring.get_keyring()` after validation cannot redirect the operation: calls remain on the validated instance and top-level helpers are unused.
- Provider/service validation is intentionally conservative and rejects whitespace, path separators, and other unsafe characters.
