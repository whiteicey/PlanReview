from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect

import app.persistence.db as db_module
from app.domain.ids import legacy_review_run_id
from app.persistence.db import create_session
from app.persistence.repository import ReviewRepository


def _create_legacy_database(database) -> None:
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE review_runs (
                id INTEGER PRIMARY KEY,
                case_id VARCHAR(128) NOT NULL UNIQUE,
                final_status VARCHAR(64) NOT NULL,
                facts JSON NOT NULL,
                stage_records JSON NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            );
            CREATE TABLE rule_results (
                id INTEGER PRIMARY KEY,
                review_run_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                rule_id VARCHAR(255) NOT NULL,
                status VARCHAR(32) NOT NULL,
                severity VARCHAR(32) NOT NULL,
                category VARCHAR(255) NOT NULL,
                parameter VARCHAR(255),
                message TEXT NOT NULL,
                evidence_span_ids JSON NOT NULL,
                involved_fact_ids JSON NOT NULL,
                needs_human_review BOOLEAN NOT NULL,
                details JSON NOT NULL,
                FOREIGN KEY(review_run_id) REFERENCES review_runs(id)
            );
            CREATE TABLE findings (
                id INTEGER PRIMARY KEY,
                review_run_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                finding_id VARCHAR(255) NOT NULL,
                origin VARCHAR(32) NOT NULL,
                category VARCHAR(255) NOT NULL,
                severity VARCHAR(32) NOT NULL,
                parameter VARCHAR(255),
                title VARCHAR(1024) NOT NULL,
                description TEXT NOT NULL,
                suggestion TEXT NOT NULL,
                rule_id VARCHAR(255),
                evidence_span_ids JSON NOT NULL,
                needs_human_review BOOLEAN NOT NULL,
                review_status VARCHAR(32) NOT NULL,
                human_note TEXT,
                reviewed_at DATETIME,
                ai_snapshot JSON NOT NULL,
                FOREIGN KEY(review_run_id) REFERENCES review_runs(id)
            );
            """
        )
        for index, review_status in enumerate(("confirmed", "rejected", "pending"), start=1):
            case_id = f"legacy-case-{index}"
            connection.execute(
                "INSERT INTO review_runs VALUES (?, ?, 'READY_FOR_HUMAN_REVIEW', '[]', '[]', ?, ?)",
                (index, case_id, f"2025-01-0{index} 01:02:03", f"2025-01-0{index} 04:05:06"),
            )
            connection.execute(
                "INSERT INTO rule_results VALUES (?, ?, 0, ?, 'FAIL', 'high', 'capacity', NULL, ?, '[\"s1\"]', '[]', 1, '{}')",
                (index, index, f"R-{index}", f"rule message {index}"),
            )
            connection.execute(
                "INSERT INTO findings VALUES (?, ?, 0, ?, 'rule', 'capacity', 'high', NULL, ?, 'description', 'suggestion', ?, '[\"s1\"]', 1, ?, ?, ?, '{}')",
                (
                    index,
                    index,
                    f"F-{index}",
                    f"finding {index}",
                    f"R-{index}",
                    review_status,
                    "first paragraph\n\nsecond paragraph" if index == 1 else None,
                    f"2025-02-0{index} 07:08:09" if index != 3 else None,
                ),
            )


def test_legacy_uuid5_is_stable_and_case_specific() -> None:
    assert legacy_review_run_id("same-case") == legacy_review_run_id("same-case")
    assert legacy_review_run_id("same-case") != legacy_review_run_id("other-case")


def test_history_migration_preserves_children_reviews_and_is_idempotent(tmp_path):
    database = tmp_path / "legacy-review.db"
    _create_legacy_database(database)

    session = create_session(database)
    repo = ReviewRepository(session)
    runs = [repo.get_run(legacy_review_run_id(f"legacy-case-{index}")) for index in range(1, 4)]

    assert all(run is not None for run in runs)
    assert [run.findings[0].review_status.value for run in runs] == [
        "confirmed", "rejected", "pending"
    ]
    assert runs[0].findings[0].human_note == "first paragraph\n\nsecond paragraph"
    assert runs[0].findings[0].reviewed_at.isoformat().startswith("2025-02-01T07:08:09")
    assert [run.rule_results[0].rule_id for run in runs] == ["R-1", "R-2", "R-3"]
    assert all(run.llm_status.value == "NOT_RUN" for run in runs)
    assert all(run.llm_finding_count == 0 for run in runs)

    # A second startup must not migrate or insert the legacy rows again.
    second = create_session(database)
    assert len(ReviewRepository(second).list_runs("legacy-case-1")) == 1
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM review_runs").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM rule_results").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 3
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

    inspector = inspect(second.bind)
    review_columns = {item["name"] for item in inspector.get_columns("review_runs")}
    assert {
        "validation_reason_code", "candidate_count", "valid_count", "rejected_count",
        "available_span_count", "selected_span_count", "selected_character_count", "coverage_ratio",
    } <= review_columns
    assert all(run.validation_reason_code is None for run in runs)
    review_indexes = {item["name"] for item in inspector.get_indexes("review_runs")}
    rule_indexes = {item["name"] for item in inspector.get_indexes("rule_results")}
    finding_indexes = {item["name"] for item in inspector.get_indexes("findings")}
    assert {"ix_review_runs_run_id", "ix_review_runs_case_created"} <= review_indexes
    assert "ix_rule_results_review_run_id" in rule_indexes
    assert "ix_findings_review_run_id" in finding_indexes
    assert any(
        item["unique"] and item["column_names"] == ["run_id"]
        for item in inspector.get_indexes("review_runs")
    )


def test_failed_migration_rolls_back_and_can_be_retried(tmp_path, monkeypatch):
    database = tmp_path / "rollback-review.db"
    _create_legacy_database(database)
    original = db_module._validate_history_migration

    def fail_validation(*_args, **_kwargs):
        raise RuntimeError("injected migration validation failure")

    monkeypatch.setattr(db_module, "_validate_history_migration", fail_validation)
    with pytest.raises(RuntimeError, match="injected migration"):
        create_session(database)

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(review_runs)")}
        assert "run_id" not in columns
        assert connection.execute("SELECT COUNT(*) FROM review_runs").fetchone()[0] == 3
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE '%__legacy_history'"
        ).fetchall()

    monkeypatch.setattr(db_module, "_validate_history_migration", original)
    retried = create_session(database)
    assert len(ReviewRepository(retried).list_runs("legacy-case-1")) == 1
