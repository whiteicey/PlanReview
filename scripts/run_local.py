"""Start the local demo server on loopback only."""

import uvicorn


if __name__ == "__main__":  # pragma: no cover - manual startup
    uvicorn.run("app.main:app", host="127.0.0.1", port=8765, reload=False)
