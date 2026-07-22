"""HTTP smoke test for asynchronous review progress, recovery and result actions."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx


def run_case(client: httpx.Client, label: str, document: Path) -> dict[str, object]:
    with document.open("rb") as stream:
        upload = client.post(
            "/api/cases",
            files={"file": (document.name, stream, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        )
    upload.raise_for_status()
    case_id = upload.json()["case_id"]
    accepted = client.post(f"/api/cases/{case_id}/review-jobs")
    if accepted.status_code != 202:
        raise RuntimeError(f"{label}: expected 202, got {accepted.status_code}")
    run_id = accepted.json()["run_id"]

    last_sequence = 0
    all_events: list[dict] = []
    statuses: list[str] = []
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/runs/{run_id}/progress",
            params={"after_sequence": last_sequence},
        )
        response.raise_for_status()
        progress = response.json()
        statuses.append(progress["run_status"])
        all_events.extend(progress["events"])
        last_sequence = progress["last_sequence"]
        if progress["run_status"] in {"READY_FOR_HUMAN_REVIEW", "FAILED", "INTERRUPTED"}:
            break
        time.sleep(0.08)
    else:
        raise TimeoutError(f"{label}: review did not reach a terminal state")

    replay = client.get(f"/api/runs/{run_id}/progress", params={"after_sequence": 0})
    replay.raise_for_status()
    replay_events = replay.json()["events"]
    if [item["sequence"] for item in replay_events] != list(range(1, len(replay_events) + 1)):
        raise RuntimeError(f"{label}: progress sequence is not contiguous")
    if len(replay_events) != last_sequence:
        raise RuntimeError(f"{label}: refresh replay did not restore every event")

    findings_response = client.get(f"/api/cases/{case_id}/runs/{run_id}/findings")
    findings_response.raise_for_status()
    findings = findings_response.json()
    reviewed = False
    exports: dict[str, dict[str, object]] = {}
    if replay.json()["run_status"] == "READY_FOR_HUMAN_REVIEW":
        if findings:
            finding_id = findings[0]["finding_id"]
            review = client.patch(
                f"/api/cases/{case_id}/runs/{run_id}/findings/{finding_id}",
                json={"review_status": "confirmed", "human_note": f"{label} 自动烟测确认"},
            )
            review.raise_for_status()
            reviewed = review.json()["review_status"] == "confirmed"
        for format_name in ("xlsx", "docx", "anonymous"):
            exported = client.get(f"/api/cases/{case_id}/exports/{format_name}")
            exported.raise_for_status()
            exports[format_name] = {
                "size": len(exported.content),
                "sha256": hashlib.sha256(exported.content).hexdigest(),
            }

    return {
        "label": label,
        "document": document.name,
        "case_id": case_id,
        "run_id": run_id,
        "status": replay.json()["run_status"],
        "poll_count": len(statuses),
        "observed_running": "RUNNING" in statuses,
        "event_count": len(replay_events),
        "cache_reused": any(item["message"] == "已读取现有解析结果" for item in replay_events),
        "profile_event_count": sum(item["event_type"] == "PROFILE_LOADED" for item in replay_events),
        "rule_started": sum(item["event_type"] == "RULE_STARTED" for item in replay_events),
        "rule_completed": sum(item["event_type"] == "RULE_COMPLETED" for item in replay_events),
        "finding_count": len(findings),
        "expert_review_saved": reviewed,
        "exports": exports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--case", action="append", required=True, metavar="LABEL=DOCX")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases: list[tuple[str, Path]] = []
    for value in args.case:
        label, separator, path = value.partition("=")
        if not separator:
            parser.error("--case must be LABEL=DOCX")
        document = Path(path).resolve()
        if not document.is_file():
            parser.error(f"missing DOCX: {document}")
        cases.append((label, document))
    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        results = [run_case(client, label, document) for label, document in cases]
    payload = {"base_url": args.base_url, "cases": results}
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

