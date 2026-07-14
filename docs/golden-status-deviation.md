# DEMO golden status deviation

The external DEMO package contains two extended status labels that are not
members of this project's three-valued RuleStatus contract:

- `VERSION-001: SUSPECTED` is represented as `FAIL` with human review.
- `EVIDENCE-001: BLOCK` is represented as `UNKNOWN` with `details.blocked: true`
  and a human-review Finding.

G-007 is an additional, deliberate contract correction. The original external
line used `CONSISTENCY-001` for a unit-conversion scenario, but its actual facts
are `开发井总数=2`, `单井设计产能=5万m³/d`, and
`总设计产能=100000m³/d`. The checked-in mirror therefore uses
`CONSISTENCY-003=PASS`, because the product operator is the coherent rule for
convertible units. This correction matches the scenario and does not weaken an
expected status.

The external source was backed up before D3 status correction as
`golden_cases_demo.jsonl.orig`; the repository mirror is the normalized source
used by tests.
