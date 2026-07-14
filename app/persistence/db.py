"""SQLite session factory for durable review metadata."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.persistence.models import Base


def create_session(db_path: Path) -> Session:
    """Create tables and return a real SQLAlchemy session bound to SQLite."""
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path.as_posix()}", future=True)

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    _upgrade_schema(engine)
    return Session(engine, expire_on_commit=False)


def _upgrade_schema(engine: Engine) -> None:
    """Apply additive SQLite columns required by newer local export metadata."""
    inspector = inspect(engine)
    required_columns = {
        "review_runs": {"evidence_text_hashes": "JSON NOT NULL DEFAULT '{}'"},
        "rule_results": {"rule_version": "VARCHAR(255)"},
    }
    with engine.begin() as connection:
        for table_name, columns in required_columns.items():
            if not inspector.has_table(table_name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, definition in columns.items():
                if column_name not in existing:
                    connection.execute(
                        text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")
                    )
