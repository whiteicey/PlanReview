"""Local-only FastAPI application.

Start with: uvicorn app.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.persistence.db import DatabaseRuntime
from app.persistence.repository import ReviewRepository
from app.review.parsed_cache import ParsedDocumentCache
from app.settings import get_settings
from app.storage.audit import warn_recovery_required
from app.security.loopback import assert_loopback_host


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    runtime = DatabaseRuntime(settings.db_path)
    runtime.initialize()
    application.state.database_runtime = runtime
    application.state.parsed_document_cache = ParsedDocumentCache(max_cases=8)
    session = runtime.session()
    try:
        ReviewRepository(session).interrupt_orphaned_runs()
        warn_recovery_required(session, settings.runtime_root)
    finally:
        session.close()
    try:
        yield
    finally:
        current_runtime = getattr(application.state, "database_runtime", runtime)
        current_runtime.dispose()


app = FastAPI(title="开发方案审查助手", version="0.1.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def sanitized_validation_error(_request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        errors.append({key: value for key, value in error.items() if key not in {"input", "ctx"}})
    return JSONResponse(status_code=422, content={"detail": errors})


app.include_router(router)
_web = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=_web, html=True), name="web")


if __name__ == "__main__":  # pragma: no cover - manual local launch only
    import uvicorn

    settings = get_settings()
    assert_loopback_host(settings.host)
    uvicorn.run(app, host=settings.host, port=settings.port)
