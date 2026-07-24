"""FastAPI application assembly."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.web.routes import router

app = FastAPI(title="Australian University Jobs Board", docs_url="/api/docs")

_STATIC = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
