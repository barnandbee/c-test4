"""Workday adapter — clean JSON API. Covers Sydney, UQ, Melbourne, Torrens.

Workday exposes a documented endpoint:
    POST https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    body: {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}

The response is paginated ({"total", "jobPostings":[...]}). ``postedOn`` is a
relative string ("Posted Today", "Posted 5 Days Ago", "Posted 30+ Days Ago"),
which we resolve to an approximate absolute date — good enough for the
"posted in last 7/30 days" facets, and honest about being approximate.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse

from app.ingest.base import Adapter, NotModified, PoliteClient
from app.normalise.core import RawJob

_PAGE_SIZE = 20
_MAX_PAGES = 50  # 1000 jobs cap — no AU uni posts more; guards against runaway loops


class WorkdayAdapter(Adapter):
    name = "workday"

    def _parts(self, university: dict) -> tuple[str, str]:
        """Return (cxs_jobs_url, job_url_prefix) supporting both Workday host
        shapes:
          * classic:  {tenant}.{dc}.myworkdayjobs.com/{site}
          * newer:    {dc}.myworkdaysite.com/recruiting/{tenant}/{site}
        Set via tenant/dc/site params, or a single `site_url` (the careers URL).
        """
        p = university["params"]
        site_url = p.get("site_url")
        if site_url:
            parts = urllib.parse.urlsplit(site_url)
            host = parts.netloc
            segs = [s for s in parts.path.split("/") if s and s != "en-US"]
            if "recruiting" in segs:  # myworkdaysite.com/recruiting/<tenant>/<site>
                i = segs.index("recruiting")
                tenant, site = segs[i + 1], segs[i + 2]
                job_prefix = f"https://{host}/en-US/recruiting/{tenant}/{site}"
            else:                      # <tenant>.<dc>.myworkdayjobs.com/<site>
                site = segs[-1] if segs else p.get("site", "")
                tenant = p.get("tenant") or host.split(".")[0]
                job_prefix = f"https://{host}/en-US/{site}"
        else:
            host = f"{p['tenant']}.{p['dc']}.myworkdayjobs.com"
            tenant, site = p["tenant"], p["site"]
            job_prefix = f"https://{host}/en-US/{site}"
        cxs = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        return cxs, job_prefix

    def _jobs_url(self, university: dict) -> str:
        return self._parts(university)[0]

    def endpoints(self, university: dict) -> list[dict]:
        # Single logical endpoint; pagination handled in fetch().
        return [{"url": self._jobs_url(university), "method": "POST"}]

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        url = self._jobs_url(university)
        jobs: list[RawJob] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            body = {"appliedFacets": {}, "limit": _PAGE_SIZE, "offset": offset, "searchText": ""}
            result = client.fetch(
                url, method="POST",
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            )
            if result.not_modified:
                raise NotModified(url)
            page = self.parse(result.content, university, url)
            if not page:
                break
            jobs.extend(page)
            total = self._last_total
            offset += _PAGE_SIZE
            if offset >= total:
                break
        return jobs

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        try:
            data = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            self._last_total = 0
            return []
        self._last_total = int(data.get("total", 0))
        _, job_prefix = self._parts(university)
        jobs: list[RawJob] = []
        for jp in data.get("jobPostings", []):
            external_path = jp.get("externalPath") or ""
            title = jp.get("title")
            if not title or not external_path:
                continue
            url = f"{job_prefix}{external_path}"
            source_id = jp.get("jobRequisitionId") or _last_segment(external_path)
            bullets = jp.get("bulletFields") or []
            jobs.append(
                RawJob(
                    source_job_id=str(source_id),
                    title=title,
                    url=url,
                    location=jp.get("locationsText"),
                    posted_at=_resolve_posted_on(jp.get("postedOn")),
                    classification_raw=_classification(bullets),
                    work_type_raw=_first_matching(bullets, ("continuing", "fixed", "casual", "contract")),
                    time_fraction_raw=_first_matching(bullets, ("full time", "part time")),
                    excerpt=None,  # Workday list view has no description; detail fetch avoided (polite)
                )
            )
        return jobs

    # populated by parse(), read by fetch() for pagination
    _last_total: int = 0


# --- helpers -----------------------------------------------------------------
def _last_segment(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1] or path


def _first_matching(bullets: list[str], needles: tuple[str, ...]) -> str | None:
    for b in bullets:
        low = str(b).lower()
        if any(n in low for n in needles):
            return str(b)
    return None


_CLASS_RE = re.compile(r"\b(hew|heo|hep)\s*\d{1,2}\b|\bacademic level [a-e]\b|\blevel [a-e]\b", re.I)


def _classification(bullets: list[str]) -> str | None:
    """Pull a HEW/Academic-Level token out of Workday's bulletFields if present."""
    for b in bullets:
        if _CLASS_RE.search(str(b)):
            return str(b)
    return None


_REL_RE = re.compile(r"posted\s+(\d+)\+?\s+day", re.I)


def _resolve_posted_on(value: str | None) -> dt.datetime | None:
    """Turn 'Posted Today' / 'Posted 5 Days Ago' / 'Posted 30+ Days Ago' into an
    approximate UTC datetime."""
    if not value:
        return None
    now = dt.datetime.now(dt.timezone.utc)
    low = value.lower()
    if "today" in low:
        return now
    if "yesterday" in low:
        return now - dt.timedelta(days=1)
    m = _REL_RE.search(low)
    if m:
        return now - dt.timedelta(days=int(m.group(1)))
    return None
