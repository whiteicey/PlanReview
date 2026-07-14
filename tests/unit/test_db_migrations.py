from __future__ import annotations

import sqlite3

from sqlalchemy import inspect

from app.persistence.db import create_session


def test_existing_review_schema_is_upgraded_for_anonymous_export_metadata(tmp_path):
    database = tmp_path / "legacy-review.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE review_runs (
                id INTEGER PRIMARY KEY,
                case_id VARCHAR(128) NOT NULL,
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
                details JSON NOT NULL
            );
            """
        )

    session = create_session(database)
    inspector = inspect(session.bind)

    assert "evidence_text_hashes" in {column["name"] for column in inspector.get_columns("review_runs")}
    assert "rule_version" in {column["name"] for column in inspector.get_columns("rule_results")}
