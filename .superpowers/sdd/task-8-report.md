# Task 8 Report

## Status

Completed with TDD. Parameter facts are extracted from recognized DOCX parameter tables and prose occurrences. Every recognized occurrence is retained as an independent fact with its original `source_span_id`; table/prose conflicts are not deduplicated. Table dimensions are copied only from present headers (`对象`, `时间/阶段` or `时间`/`阶段`, `统计口径`, `条件`), while missing dimensions remain `None`.

## Commits

- `20f9baaab9d598a9d8ec5ad53a735c40c7dcef42` — `feat: extract parameter facts from DOCX`

## Tests and exact output

Initial focused run after adding tests, before implementation:

```text
python -m pytest tests/unit/test_parameters.py -v
4 failed, 1 warning in 0.28s
ModuleNotFoundError: No module named 'app.extraction.parameters'
```

Focused verification:

```text
python -m pytest tests/unit/test_parameters.py -v
4 passed, 1 warning in 0.35s
```

Full-suite verification:

```text
python -m pytest -q
39 passed, 1 warning in 0.69s
```

## Concerns

Pytest continues to emit the existing configuration warning `Unknown config option: asyncio_mode`; this task did not change that configuration. Prose unit recognition is deliberately limited to explicit supported unit forms, so unsupported prose units are left unset rather than guessed. Table units are preserved verbatim.
