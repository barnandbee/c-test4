"""Scheduled ingestion worker.

Runs a full refresh a handful of times per day (default 3 = every 8h), plus once
shortly after startup. Kept intentionally infrequent — job postings change slowly
and CRAWLING.md forbids hammering the source sites.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.blocking import BlockingScheduler

from app.config import get_settings
from app.ingest.runner import init_db, run_full_refresh

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scheduler")


def _refresh() -> None:
    log.info("starting scheduled refresh")
    try:
        run_id = run_full_refresh()
        log.info("refresh complete: run %s", run_id)
    except Exception:  # never let one bad run kill the scheduler
        log.exception("refresh failed")


def main() -> None:
    settings = get_settings()
    init_db()
    every_hours = max(1, 24 // max(1, settings.refresh_times_per_day))

    scheduler = BlockingScheduler(timezone="UTC")
    # Kick off ~1 min after boot so the DB/web are up, then on the interval.
    scheduler.add_job(_refresh, "interval", hours=every_hours,
                      next_run_time=None, id="refresh", max_instances=1,
                      coalesce=True, misfire_grace_time=3600)
    scheduler.add_job(_refresh, "date", id="startup-refresh")
    log.info("scheduler up: refreshing every %sh (%s/day)", every_hours,
             settings.refresh_times_per_day)
    scheduler.start()


if __name__ == "__main__":
    main()
