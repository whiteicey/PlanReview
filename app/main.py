"""Local-only FastAPI application.

Start with: uvicorn app.main:app --host 127.0.0.1 --port 8765
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.settings import get_settings

app = FastAPI(title="开发方案审查助手", version="0.1.0")
app.include_router(router)
_web = Path(__file__).resolve().parent.parent / "web"
app.mount("/", StaticFiles(directory=_web, html=True), name="web")


if __name__ == "__main__":  # pragma: no cover - manual local launch only
    import uvicorn

    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)
