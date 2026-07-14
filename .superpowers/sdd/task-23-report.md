# Task 23 report

## Review status

The committed green implementation remains intact, but the latest architectural
review identified P0/P1 design issues that require a deeper refactor: compatibility
operators still contain profile branches and imperative legacy behavior, and the
engine/importer retain rule-ID policy branches. I attempted the first refactor
pass and reverted it because it made the external golden red; no incomplete
refactor is committed.

## Current verified baseline

- External golden with actual root: `15 passed, 1 warning in 0.64s`
- Full suite: `256 passed, 11 skipped, 1 warning in 7.71s`
- Direct compatibility safety test currently passes for one mismatch scenario.

## Outstanding architectural work

1. Move all legacy overlay translation to a typed schema/registry and remove
   importer rule-ID/parameter chains.
2. Route every compatibility comparison through the shared complete five-key
   scope selector; add all four dimension mismatch probes for every operator.
3. Replace trigger-only semantic conclusions with fact/evidence relationship
   operators.
4. Move all human-review policy to `RuleDefinition.requires_human_review` or
   explicit params and remove engine rule-ID special cases.
5. Keep one authoritative operator registry while preserving the existing public
   base operator-name contract.

This report intentionally does not claim architectural completion. The green
baseline is preserved so the next refactor can proceed incrementally with
failure-first tests.
