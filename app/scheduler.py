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


def interval_hours() -> int:
    """Hours between refreshes, derived from REFRESH_TIMES_PER_DAY."""
    return max(1, 24 // max(1, get_settings().refresh_times_per_day))


def schedule_refresh(scheduler, run_at_startup: bool = True) -> None:
    """Add the refresh jobs to any APScheduler instance (blocking or background)."""
    hours = interval_hours()
    scheduler.add_job(_refresh, "interval", hours=hours, id="refresh",
                      max_instances=1, coalesce=True, misfire_grace_time=3600)
    if run_at_startup:
        scheduler.add_job(_refresh, "date", id="startup-refresh")
    log.info("scheduled refresh every %sh (%s/day)", hours,
             get_settings().refresh_times_per_day)


def main() -> None:
    init_db()
    scheduler = BlockingScheduler(timezone="UTC")
    schedule_refresh(scheduler)
    scheduler.start()


if __name__ == "__main__":
    main()
