"""On-demand crawl trigger for hosts without shell/worker access (e.g. Render free).

Runs a full refresh in a background thread so an HTTP request can kick it off and
return immediately, while `/admin` shows progress. A lock prevents overlapping
crawls (which would breach the per-domain rate limits in CRAWLING.md).
"""
from __future__ import annotations

import datetime as dt
import logging
import threading

from app.ingest.runner import run_full_refresh

log = logging.getLogger("trigger")

_lock = threading.Lock()
_state: dict = {"running": False, "started_at": None, "last_finished_at": None,
                "last_run_id": None, "last_error": None}


def is_running() -> bool:
    return _state["running"]


def status() -> dict:
    return dict(_state)


def _run(only: list[str] | None) -> None:
    try:
        run_id = run_full_refresh(only=only)
        _state["last_run_id"] = run_id
        _state["last_error"] = None
    except Exception as exc:  # noqa: BLE001
        _state["last_error"] = f"{type(exc).__name__}: {exc}"
        log.exception("manual refresh failed")
    finally:
        _state["running"] = False
        _state["last_finished_at"] = dt.datetime.now(dt.timezone.utc)
        _lock.release()


def start(only: list[str] | None = None) -> bool:
    """Start a background refresh. Returns False if one is already running."""
    if not _lock.acquire(blocking=False):
        return False
    _state.update(running=True, started_at=dt.datetime.now(dt.timezone.utc))
    threading.Thread(target=_run, args=(only,), daemon=True, name="manual-refresh").start()
    return True
