from __future__ import annotations

import json

from app.review.ledgers import LifecycleLedger


def test_ledger_envelope_and_summary_survive_capacity_limit() -> None:
    ledger = LifecycleLedger("packet_lifecycle", max_entries=1, max_bytes=10_000)
    assert ledger.append({"stage": "GENERATED", "decision": "KEPT"}, summary_keys=("stage", "decision"))
    assert not ledger.append({"stage": "BUDGET", "decision": "DROPPED"}, summary_keys=("stage", "decision"))
    payload = ledger.to_dict()
    assert payload["ledger_schema_version"] == "v1"
    assert payload["ledger_entry_count"] == 1
    assert payload["ledger_truncated"] is True
    assert payload["summary"]["stage:GENERATED"] == 1
    assert payload["summary"]["stage:BUDGET"] == 1
    assert payload["entries"] == [{"stage": "GENERATED", "decision": "KEPT"}]


def test_ledger_byte_limit_does_not_raise_or_change_summary() -> None:
    ledger = LifecycleLedger("ai_candidate_lifecycle", max_entries=100, max_bytes=1)
    assert not ledger.append({"candidate_id": "c1", "decision": "DISCARDED"}, summary_keys=("decision",))
    payload = ledger.to_dict()
    assert payload["ledger_truncated"] is True
    assert payload["ledger_entry_count"] == 0
    assert payload["summary"]["decision:DISCARDED"] == 1
    json.dumps(payload, ensure_ascii=False)
