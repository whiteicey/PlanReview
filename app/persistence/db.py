"""SQLite session factory and idempotent schema upgrades."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from app.domain.ids import legacy_review_run_id
from app.persistence.models import Base, CaseORM, FindingORM, ReviewRunORM, RuleResultORM


class DatabaseRuntime:
    """One application-scoped Engine and a factory for request-scoped Sessions."""

    def __init__(self, db_path: Path) -> None:
        self.path = Path(db_path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.path.as_posix()}",
            future=True,
            connect_args={"check_same_thread": False},
        )
        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )
        self._initialized = False
        self._initialize_lock = RLock()
        event.listen(self.engine, "connect", _configure_sqlite_connection)

    @property
    def initialized(self) -> bool:
        return self._initialized

    def initialize(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            # Migrate legacy parents before create_all creates new child tables;
            # otherwise SQLite rewrites their foreign key to the temporary
            # legacy table when review_runs is renamed.
            CaseORM.__table__.create(self.engine, checkfirst=True)
            _upgrade_schema(self.engine)
            Base.metadata.create_all(self.engine)
            self._initialized = True

    def session(self) -> Session:
        if not self._initialized:
            raise RuntimeError("database runtime has not been initialized")
        return self.session_factory()

    def dispose(self) -> None:
        self.engine.dispose()


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
    finally:
        cursor.close()


def create_session(db_path: Path) -> Session:
    """Compatibility helper for scripts/tests; applications use DatabaseRuntime."""
    runtime = DatabaseRuntime(db_path)
    runtime.initialize()
    session = runtime.session()
    # Retain the runtime for the life of this standalone Session.
    session.info["database_runtime"] = runtime
    return session


def _upgrade_schema(engine: Engine) -> None:
    """Apply idempotent SQLite upgrades without losing business records."""

    if _history_migration_required(engine):
        _migrate_review_run_history(engine)

    inspector = inspect(engine)
    required_columns = {
        "review_runs": {
            "evidence_text_hashes": "JSON NOT NULL DEFAULT '{}'",
            "evidence_locations": "JSON NOT NULL DEFAULT '{}'",
            "llm_provider": "VARCHAR(128)",
            "llm_model": "VARCHAR(255)",
            "llm_status": "VARCHAR(32) NOT NULL DEFAULT 'NOT_RUN'",
            "llm_finding_count": "INTEGER NOT NULL DEFAULT 0",
            "llm_error_summary": "TEXT",
            "validation_reason_code": "VARCHAR(64)",
            "candidate_count": "INTEGER",
            "valid_count": "INTEGER",
            "rejected_count": "INTEGER",
            "available_span_count": "INTEGER",
            "selected_span_count": "INTEGER",
            "selected_character_count": "INTEGER",
            "coverage_ratio": "FLOAT",
            "git_sha": "VARCHAR(64)",
            "prompt_version": "VARCHAR(128)",
            "evidence_selector_version": "VARCHAR(128)",
            "max_tokens": "INTEGER",
            "timeout": "FLOAT",
            "temperature": "FLOAT",
            "batch_count": "INTEGER NOT NULL DEFAULT 0",
            "batch_metrics": "JSON NOT NULL DEFAULT '[]'",
            "premerge_finding_count": "INTEGER NOT NULL DEFAULT 0",
            "postmerge_finding_count": "INTEGER NOT NULL DEFAULT 0",
            "deduplicated_finding_count": "INTEGER NOT NULL DEFAULT 0",
            "stop_reason": "VARCHAR(64)",
            "ai_guard_rejections": "JSON NOT NULL DEFAULT '[]'",
            "deduplication_records": "JSON NOT NULL DEFAULT '[]'",
            "packet_lifecycle_ledger": "JSON NOT NULL DEFAULT '{}'",
            "ai_candidate_lifecycle_ledger": "JSON NOT NULL DEFAULT '{}'",
            "rule_metrics": "JSON NOT NULL DEFAULT '{}'",
            "worker_token": "VARCHAR(36)",
        },
        "rule_results": {"rule_version": "VARCHAR(255)"},
        "findings": {
            "reviewed_at": "DATETIME",
            "is_expert_experience": "BOOLEAN NOT NULL DEFAULT 0",
            "experience_saved_at": "DATETIME",
            "experience_updated_at": "DATETIME",
        },
    }
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.execute(
                        text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {definition}')
                    )


def _history_migration_required(engine: Engine) -> bool:
    inspector = inspect(engine)
    if not inspector.has_table("review_runs"):
        return False
    columns = {column["name"] for column in inspector.get_columns("review_runs")}
    if "run_id" not in columns:
        return True
    unique_case_constraint = any(
        constraint.get("column_names") == ["case_id"]
        for constraint in inspector.get_unique_constraints("review_runs")
    )
    unique_case_index = any(
        index.get("unique") and index.get("column_names") == ["case_id"]
        for index in inspector.get_indexes("review_runs")
    )
    return unique_case_constraint or unique_case_index


def _migrate_review_run_history(engine: Engine) -> None:
    """Rebuild the run parent and children transactionally for append-only history."""

    legacy_names = {
        "review_runs": "review_runs__legacy_history",
        "rule_results": "rule_results__legacy_history",
        "findings": "findings__legacy_history",
    }
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.commit()
        try:
            # SQLite may otherwise defer BEGIN until the first DML statement,
            # allowing an earlier ALTER TABLE to escape rollback.  Start the
            # transaction explicitly before any schema change.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                existing_tables = {
                    row[0]
                    for row in connection.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                if "review_runs" not in existing_tables:
                    return
                before = {
                    table: _table_count(connection, table)
                    for table in legacy_names
                    if table in existing_tables
                }

                # Named indexes occupy a database-wide namespace after table
                # renames, so remove only those attached to the soon-to-be
                # legacy tables. SQLite autoindexes have sql=NULL and are kept.
                index_rows = connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name IN ('review_runs','rule_results','findings') "
                    "AND sql IS NOT NULL"
                ).all()
                for (index_name,) in index_rows:
                    connection.exec_driver_sql(f'DROP INDEX "{index_name}"')

                for table, legacy in legacy_names.items():
                    if table in existing_tables:
                        connection.exec_driver_sql(f'ALTER TABLE "{table}" RENAME TO "{legacy}"')

                ReviewRunORM.__table__.create(connection)
                RuleResultORM.__table__.create(connection)
                FindingORM.__table__.create(connection)

                _copy_legacy_runs(connection, legacy_names["review_runs"])
                if "rule_results" in before:
                    _copy_legacy_children(
                        connection,
                        legacy_names["rule_results"],
                        "rule_results",
                        {"rule_version": None},
                    )
                if "findings" in before:
                    _copy_legacy_children(
                        connection,
                        legacy_names["findings"],
                        "findings",
                        {
                            "is_expert_experience": 0,
                            "experience_saved_at": None,
                            "experience_updated_at": None,
                        },
                    )

                _validate_history_migration(connection, before)

                for table in ("findings", "rule_results", "review_runs"):
                    if table in before:
                        connection.exec_driver_sql(
                            f'DROP TABLE "{legacy_names[table]}"'
                        )

                violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
                if violations:
                    raise RuntimeError("foreign key validation failed after review history migration")
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.commit()


def _copy_legacy_runs(connection: Connection, legacy_table: str) -> None:
    columns = _table_columns(connection, legacy_table)
    rows = connection.exec_driver_sql(f'SELECT * FROM "{legacy_table}" ORDER BY id').mappings()
    now = datetime.now(timezone.utc).isoformat()
    target_columns = (
        "id",
        "run_id",
        "case_id",
        "final_status",
        "facts",
        "stage_records",
        "evidence_text_hashes",
        "evidence_locations",
        "llm_provider",
        "llm_model",
        "llm_status",
        "llm_finding_count",
        "llm_error_summary",
        "batch_count",
        "batch_metrics",
        "premerge_finding_count",
        "postmerge_finding_count",
        "deduplicated_finding_count",
        "ai_guard_rejections",
        "deduplication_records",
        "packet_lifecycle_ledger",
        "ai_candidate_lifecycle_ledger",
        "rule_metrics",
        "created_at",
        "updated_at",
    )
    placeholders = ",".join("?" for _ in target_columns)
    quoted = ",".join(f'"{column}"' for column in target_columns)
    for row in rows:
        case_id = row["case_id"]
        if connection.exec_driver_sql(
            "SELECT 1 FROM cases WHERE case_id=?", (case_id,)
        ).first() is None:
            connection.exec_driver_sql(
                "INSERT INTO cases (case_id, statistics) VALUES (?, '{}')", (case_id,)
            )
        created_at = row["created_at"] if "created_at" in columns and row["created_at"] else now
        updated_at = row["updated_at"] if "updated_at" in columns and row["updated_at"] else created_at
        values = (
            row["id"],
            legacy_review_run_id(case_id),
            case_id,
            row["final_status"],
            row["facts"] if "facts" in columns and row["facts"] is not None else "[]",
            row["stage_records"] if "stage_records" in columns and row["stage_records"] is not None else "[]",
            row["evidence_text_hashes"] if "evidence_text_hashes" in columns and row["evidence_text_hashes"] is not None else "{}",
            row["evidence_locations"] if "evidence_locations" in columns and row["evidence_locations"] is not None else "{}",
            row["llm_provider"] if "llm_provider" in columns else None,
            row["llm_model"] if "llm_model" in columns else None,
            row["llm_status"] if "llm_status" in columns and row["llm_status"] else "NOT_RUN",
            row["llm_finding_count"] if "llm_finding_count" in columns and row["llm_finding_count"] is not None else 0,
            row["llm_error_summary"] if "llm_error_summary" in columns else None,
            row["batch_count"] if "batch_count" in columns and row["batch_count"] is not None else 0,
            row["batch_metrics"] if "batch_metrics" in columns and row["batch_metrics"] is not None else "[]",
            row["premerge_finding_count"] if "premerge_finding_count" in columns and row["premerge_finding_count"] is not None else 0,
            row["postmerge_finding_count"] if "postmerge_finding_count" in columns and row["postmerge_finding_count"] is not None else 0,
            row["deduplicated_finding_count"] if "deduplicated_finding_count" in columns and row["deduplicated_finding_count"] is not None else 0,
            row["ai_guard_rejections"] if "ai_guard_rejections" in columns and row["ai_guard_rejections"] is not None else "[]",
            row["deduplication_records"] if "deduplication_records" in columns and row["deduplication_records"] is not None else "[]",
            row["packet_lifecycle_ledger"] if "packet_lifecycle_ledger" in columns and row["packet_lifecycle_ledger"] is not None else '{"ledger_schema_version":"v1","ledger_entry_count":0,"ledger_truncated":false,"ledger_size_bytes":0,"entries":[],"summary":{}}',
            row["ai_candidate_lifecycle_ledger"] if "ai_candidate_lifecycle_ledger" in columns and row["ai_candidate_lifecycle_ledger"] is not None else '{"ledger_schema_version":"v1","ledger_entry_count":0,"ledger_truncated":false,"ledger_size_bytes":0,"entries":[],"summary":{}}',
            row["rule_metrics"] if "rule_metrics" in columns and row["rule_metrics"] is not None else "{}",
            created_at,
            updated_at,
        )
        connection.exec_driver_sql(
            f'INSERT INTO review_runs ({quoted}) VALUES ({placeholders})', values
        )


def _copy_legacy_children(
    connection: Connection,
    legacy_table: str,
    target_table: str,
    missing_defaults: dict[str, Any],
) -> None:
    source_columns = _table_columns(connection, legacy_table)
    target_columns = [row[1] for row in connection.exec_driver_sql(f'PRAGMA table_info("{target_table}")')]
    insert_columns: list[str] = []
    expressions: list[str] = []
    for column in target_columns:
        if column in source_columns:
            insert_columns.append(column)
            expressions.append(f'"{column}"')
        elif column in missing_defaults:
            insert_columns.append(column)
            default = missing_defaults[column]
            expressions.append("NULL" if default is None else repr(default))
    quoted_columns = ",".join(f'"{column}"' for column in insert_columns)
    connection.exec_driver_sql(
        f'INSERT INTO "{target_table}" ({quoted_columns}) '
        f'SELECT {",".join(expressions)} FROM "{legacy_table}" ORDER BY id'
    )


def _validate_history_migration(connection: Connection, before: dict[str, int]) -> None:
    for table, expected in before.items():
        if _table_count(connection, table) != expected:
            raise RuntimeError(f"row count mismatch for {table}")
    duplicate = connection.exec_driver_sql(
        "SELECT run_id FROM review_runs GROUP BY run_id HAVING COUNT(*) > 1"
    ).first()
    if duplicate is not None:
        raise RuntimeError("duplicate run_id generated during migration")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        raise RuntimeError("foreign key validation failed during review history migration")


def _table_count(connection: Connection, table_name: str) -> int:
    return int(connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table_name}"').scalar_one())


def _table_columns(connection: Connection, table_name: str) -> set[str]:
    return {
        row[1]
        for row in connection.exec_driver_sql(f'PRAGMA table_info("{table_name}")')
    }
