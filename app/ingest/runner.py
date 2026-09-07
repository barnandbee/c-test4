"""Ingestion runner: config-driven full refresh.

For each university: pick its adapter, fetch politely, normalise, then run a
per-university *safe* reconciliation. A failing or suspicious adapter records its
health and is skipped — it never takes down the run or blanks live listings.

CLI:
    python -m app.ingest.runner              # full refresh
    python -m app.ingest.runner --only uwa   # single university
    python -m app.ingest.runner --verify     # just probe every endpoint (needs egress)
    python -m app.ingest.runner --discover   # auto-detect each ATS from its careers page
    python -m app.ingest.runner --init-db    # create tables
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings, load_universities
from app.db import SessionLocal, engine
from app.ingest.base import NotModified, PoliteClient, RobotsDisallowed
from app.ingest.discover import detect
from app.ingest.reconcile import plan_reconciliation
from app.ingest.registry import get_adapter
from app.models import (
    AdapterRun,
    Base,
    CrawlRun,
    HttpCache,
    Listing,
    SourceResolution,
    StagingListing,
    utcnow,
)
from app.normalise.core import normalise_record

# Re-detect an already-resolved source at most this often.
_RESOLUTION_TTL = dt.timedelta(days=7)

_UPSERT_COLUMNS = (
    "source_job_id", "university", "university_slug", "state", "campus_location",
    "title", "url", "posted_at", "closes_at", "work_type", "time_fraction",
    "role_family", "classification_raw", "level_band", "level_scale",
    "salary_min", "salary_max", "salary_raw", "discipline", "remote_flag", "excerpt",
)


def init_db() -> None:
    Base.metadata.create_all(engine)


# --- HTTP cache <-> DB -------------------------------------------------------
def _load_cache(session: Session) -> dict:
    return {
        row.url: {"etag": row.etag, "last_modified": row.last_modified}
        for row in session.scalars(select(HttpCache))
    }


def _save_cache(session: Session, cache: dict) -> None:
    for url, validators in cache.items():
        stmt = pg_insert(HttpCache).values(
            url=url, etag=validators.get("etag"),
            last_modified=validators.get("last_modified"), fetched_at=utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["url"],
            set_={"etag": stmt.excluded.etag,
                   "last_modified": stmt.excluded.last_modified,
                   "fetched_at": stmt.excluded.fetched_at},
        )
        session.execute(stmt)


# --- per-university reconciliation ------------------------------------------
def _upsert_listings(session: Session, records: list[dict]) -> None:
    if not records:
        return
    now = utcnow()
    rows = [
        {**{k: r.get(k) for k in _UPSERT_COLUMNS},
         "id": r["id"], "first_seen_at": now, "last_seen_at": now, "status": "open"}
        for r in records
    ]
    stmt = pg_insert(Listing).values(rows)
    update_set = {c: getattr(stmt.excluded, c) for c in _UPSERT_COLUMNS}
    update_set["last_seen_at"] = stmt.excluded.last_seen_at
    update_set["status"] = "open"
    # first_seen_at deliberately NOT updated — preserve original discovery time.
    stmt = stmt.on_conflict_do_update(index_elements=["id"], set_=update_set)
    session.execute(stmt)


def _close_listings(session: Session, ids: list[str]) -> None:
    if not ids:
        return
    session.execute(
        update(Listing).where(Listing.id.in_(ids)).values(status="closed", last_seen_at=utcnow())
    )


def _expire_past_closing(session: Session, slug: str) -> None:
    """Mark listings whose closing date has passed as expired (independent of crawl)."""
    session.execute(
        update(Listing)
        .where(Listing.university_slug == slug, Listing.status == "open",
               Listing.closes_at.is_not(None), Listing.closes_at < utcnow())
        .values(status="expired")
    )


def resolve_source(client: PoliteClient, university: dict, session: Session) -> dict:
    """Return the effective university record (adapter + params) to crawl with.

    Discovery-first: fetch the careers page, detect the real ATS endpoint, and
    cache it. This makes wrong config guesses self-correcting on a host with open
    egress. Falls back to the configured adapter/params when discovery can't run
    or finds nothing.
    """
    slug = university["slug"]
    cached = session.get(SourceResolution, slug)
    if cached and cached.ok and (utcnow() - cached.resolved_at) < _RESOLUTION_TTL:
        return {**university, "adapter": cached.adapter, "params": json.loads(cached.params_json)}

    careers_url = university.get("careers_url")
    detection = None
    if careers_url:
        try:
            res = client.fetch(careers_url)
            if not res.not_modified:
                body = res.content.decode("utf-8", errors="replace")
                detection = detect(body, res.final_url or res.url)
        except Exception:  # discovery is best-effort; fall back to config
            detection = None

    if detection:
        session.merge(SourceResolution(
            university_slug=slug, adapter=detection.adapter,
            params_json=json.dumps(detection.params), matched_url=detection.matched_url,
            source="discovered", ok=True, resolved_at=utcnow(),
        ))
        return {**university, "adapter": detection.adapter, "params": detection.params}

    return university


def run_one(client: PoliteClient, university: dict, session: Session, run_id: int) -> AdapterRun:
    slug = university["slug"]
    university = resolve_source(client, university, session)
    adapter = get_adapter(university["adapter"])
    started = time.monotonic()
    health = AdapterRun(run_id=run_id, university_slug=slug, adapter=adapter.name, status="ok")

    try:
        raw_jobs = adapter.fetch(client, university)
    except RobotsDisallowed as exc:
        health.status, health.error = "skipped_robots", str(exc)
        session.add(health)
        return health
    except NotModified:
        prev = session.scalar(
            select(func.count()).select_from(Listing)
            .where(Listing.university_slug == slug, Listing.status == "open")
        ) or 0
        health.status, health.records_returned = "no_change", prev
        health.duration_ms = int((time.monotonic() - started) * 1000)
        session.add(health)
        return health
    except Exception as exc:  # any adapter error is contained here
        health.status, health.error = "failed", f"{type(exc).__name__}: {exc}"
        health.duration_ms = int((time.monotonic() - started) * 1000)
        session.add(health)
        return health

    # Normalise + dedupe by stable id.
    records: dict[str, dict] = {}
    for raw in raw_jobs:
        rec = normalise_record(raw, university)
        records[rec["id"]] = rec
    records_list = list(records.values())

    # Staging (for debugging a run without re-crawling). `rec` already carries
    # university_slug via _UPSERT_COLUMNS, so only run_id is passed separately.
    session.add_all(
        StagingListing(run_id=run_id, **{k: rec.get(k) for k in (*_UPSERT_COLUMNS, "id")})
        for rec in records_list
    )

    # Reconcile against currently-open listings.
    existing_open = set(session.scalars(
        select(Listing.id).where(Listing.university_slug == slug, Listing.status == "open")
    ))
    plan = plan_reconciliation(
        existing_open_ids=existing_open,
        seen_ids=set(records),
        prev_count=len(existing_open),
        new_count=len(records_list),
        min_ratio=get_settings().min_yield_ratio,
    )
    if plan.upsert:
        _upsert_listings(session, records_list)
    _close_listings(session, plan.close_ids)
    _expire_past_closing(session, slug)

    health.status = "suspect_low_yield" if plan.suspect else "ok"
    health.records_returned = len(records_list)
    health.duration_ms = int((time.monotonic() - started) * 1000)
    health.error = plan.reason if plan.suspect else None
    session.add(health)
    return health


def run_full_refresh(only: list[str] | None = None) -> int:
    """Run a full refresh across all (or selected) universities. Returns run id."""
    universities = load_universities()
    if only:
        universities = [u for u in universities if u["slug"] in set(only)]

    session = SessionLocal()
    try:
        run = CrawlRun(status="running")
        session.add(run)
        session.flush()  # get run.id
        cache = _load_cache(session)
        client = PoliteClient(cache=cache)
        ok = failed = 0
        try:
            for uni in universities:
                try:
                    health = run_one(client, uni, session, run.id)
                    status = health.status
                except Exception as exc:  # noqa: BLE001 — a bug for one uni must not kill the run
                    session.rollback()
                    session.add(AdapterRun(
                        run_id=run.id, university_slug=uni["slug"],
                        adapter=uni.get("adapter", "?"), status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                    ))
                    status = "failed"
                if status in {"ok", "no_change", "suspect_low_yield"}:
                    ok += 1
                else:
                    failed += 1
                session.commit()  # commit per-uni: one failure never rolls back others
        finally:
            client.close()
        _save_cache(session, cache)
        run.universities_ok, run.universities_failed = ok, failed
        run.status, run.finished_at = "complete", utcnow()
        session.commit()
        return run.id
    finally:
        session.close()


def verify_endpoints() -> None:
    """Probe every configured endpoint and report HTTP status. Needs egress."""
    client = PoliteClient()
    try:
        for uni in load_universities():
            adapter = get_adapter(uni["adapter"])
            for spec in adapter.endpoints(uni):
                url = spec["url"]
                try:
                    res = client.fetch(url, spec.get("method", "GET"), **spec.get("kwargs", {}))
                    print(f"[{res.status_code}] {uni['slug']:12} {uni['adapter']:10} {url}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[ERR] {uni['slug']:12} {uni['adapter']:10} {url}  -> {exc}")
    finally:
        client.close()


def discover_sources(only: list[str] | None = None) -> None:
    """Fetch each careers page, detect its ATS, and cache the resolution. Prints a
    report and persists results so the next crawl uses the discovered endpoints.
    Needs egress."""
    init_db()
    universities = load_universities()
    if only:
        universities = [u for u in universities if u["slug"] in set(only)]
    client = PoliteClient()
    session = SessionLocal()
    found = 0
    try:
        for uni in universities:
            slug = uni["slug"]
            careers_url = uni.get("careers_url", "")
            try:
                res = client.fetch(careers_url)
                body = res.content.decode("utf-8", errors="replace")
                det = detect(body, res.final_url or res.url)
            except Exception as exc:  # noqa: BLE001
                print(f"[ERR ] {slug:12} {careers_url}  -> {exc}")
                continue
            if det:
                found += 1
                session.merge(SourceResolution(
                    university_slug=slug, adapter=det.adapter,
                    params_json=json.dumps(det.params), matched_url=det.matched_url,
                    source="discovered", ok=True, resolved_at=utcnow(),
                ))
                session.commit()
                print(f"[ OK ] {slug:12} {det.adapter:14} {det.params}  (config said {uni['adapter']})")
            else:
                print(f"[ -- ] {slug:12} no ATS detected on {careers_url}  (config: {uni['adapter']})")
        print(f"\nDiscovered {found}/{len(universities)} sources. Run a refresh to crawl them.")
    finally:
        client.close()
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aus Uni Jobs Board ingestion runner")
    parser.add_argument("--only", nargs="+", help="restrict to these university slugs")
    parser.add_argument("--verify", action="store_true", help="probe endpoints only")
    parser.add_argument("--discover", action="store_true",
                        help="auto-detect each ATS from its careers page and cache it")
    parser.add_argument("--init-db", action="store_true", help="create tables and exit")
    args = parser.parse_args(argv)

    if args.init_db:
        init_db()
        print("tables created")
        return 0
    if args.verify:
        verify_endpoints()
        return 0
    if args.discover:
        discover_sources(only=args.only)
        return 0

    start = dt.datetime.now()
    run_id = run_full_refresh(only=args.only)
    print(f"refresh complete (run {run_id}) in {(dt.datetime.now() - start).total_seconds():.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
