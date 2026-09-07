"""FastAPI application assembly."""
from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import get_settings
from app.web.routes import router

log = logging.getLogger("app")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Optionally run the ingestion scheduler inside the web process.

    Enabled with RUN_SCHEDULER_IN_WEB=true — used on hosts without a separate
    worker (e.g. Render's free tier). docker-compose leaves this off and runs a
    dedicated `worker` service instead.
    """
    scheduler = None
    if get_settings().run_scheduler_in_web:
        from apscheduler.schedulers.background import BackgroundScheduler

        from app.ingest.runner import init_db
        from app.scheduler import schedule_refresh

        init_db()
        scheduler = BackgroundScheduler(timezone="UTC")
        schedule_refresh(scheduler)
        scheduler.start()
        log.info("in-process scheduler started")
    try:
        yield
    finally:
        if scheduler:
            scheduler.shutdown(wait=False)


# Public docs disabled; Swagger is served behind the admin login (see routes).
app = FastAPI(title="Australian University Jobs Board",
              docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

_STATIC = Path(__file__).resolve().parent / "web" / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")
app.include_router(router)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
