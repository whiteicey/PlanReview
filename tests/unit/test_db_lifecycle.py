from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.routes import get_db_session
from app.persistence.db import DatabaseRuntime


def test_runtime_initializes_schema_once_and_uses_sqlite_safety_pragmas(monkeypatch, tmp_path):
    import app.persistence.db as database

    calls = 0
    original = database._upgrade_schema

    def counted(engine):
        nonlocal calls
        calls += 1
        return original(engine)

    monkeypatch.setattr(database, "_upgrade_schema", counted)
    runtime = DatabaseRuntime(tmp_path / "review.db")
    runtime.initialize()
    runtime.initialize()

    with runtime.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 5_000
    assert calls == 1
    runtime.dispose()


def test_request_dependency_reuses_engine_and_closes_sessions_on_success_and_error(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    application = FastAPI()

    @application.get("/ok")
    def ok(session: Session = Depends(get_db_session)):
        return {"value": session.execute(text("SELECT 1")).scalar_one()}

    @application.get("/error")
    def error(session: Session = Depends(get_db_session)):
        session.execute(text("SELECT 1"))
        raise RuntimeError("expected")

    client = TestClient(application, raise_server_exceptions=False)
    assert client.get("/ok").json() == {"value": 1}
    runtime = application.state.database_runtime
    engine = runtime.engine
    assert client.get("/ok").status_code == 200
    assert application.state.database_runtime.engine is engine
    assert engine.pool.checkedout() == 0
    assert client.get("/error").status_code == 500
    assert engine.pool.checkedout() == 0
    runtime.dispose()


def test_two_concurrent_read_requests_use_independent_sessions(monkeypatch, tmp_path):
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.settings import get_settings

    get_settings.cache_clear()
    application = FastAPI()

    @application.get("/read")
    def read(session: Session = Depends(get_db_session)):
        return {"session": id(session), "value": session.execute(text("SELECT 1")).scalar_one()}

    client = TestClient(application)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.get("/read").json(), range(2)))
    assert {item["value"] for item in results} == {1}
    assert len({item["session"] for item in results}) == 2
    application.state.database_runtime.dispose()


def test_application_shutdown_disposes_engine_and_releases_database(monkeypatch, tmp_path):
    database = tmp_path / "storage" / "review.db"
    monkeypatch.setenv("REVIEW_STORAGE_ROOT", str(tmp_path / "storage"))
    from app.main import lifespan
    from app.settings import get_settings

    get_settings.cache_clear()
    application = FastAPI(lifespan=lifespan)
    with TestClient(application):
        assert database.exists()
    database.unlink()
    assert not database.exists()
