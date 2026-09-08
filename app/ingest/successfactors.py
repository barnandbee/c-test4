"""SAP SuccessFactors adapter — the "Recruiting Marketing" career site.

SuccessFactors career sites (e.g. ``careers.cqu.edu.au``) server-render a search
page at ``/search/`` with job tiles that link to ``/job/<slug>/<jobReqId>/``.
Results are paginated with ``?startrow=N`` in steps of the page size. We key off
the stable job-URL pattern rather than brittle wrapper classes, and stop as soon
as a page yields no new job ids (robust to whatever the page size is).
"""
from __future__ import annotations

import re
import urllib.parse

from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, NotModified, PoliteClient
from app.normalise.core import RawJob

# /job/<slug>/<numeric req id>/
_JOB_PATH_RE = re.compile(r"^/job/[^/]+/(\d+)/?$")
_PAGE_STEP = 25   # SuccessFactors default result page size
_MAX_PAGES = 40


class SuccessFactorsAdapter(Adapter):
    name = "successfactors"

    def endpoints(self, university: dict) -> list[dict]:
        url = university["params"].get("listing_url")
        return [{"url": url}] if url else []

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        specs = self.endpoints(university)
        if not specs:
            return []
        base = specs[0]["url"]
        seen: set[str] = set()
        jobs: list[RawJob] = []
        for page in range(_MAX_PAGES):
            url = _with_startrow(base, page * _PAGE_STEP)
            result = client.fetch(url)
            if result.not_modified:
                if page == 0:
                    raise NotModified(base)
                break
            page_jobs = self.parse(result.content, university, result.final_url or url)
            new = [j for j in page_jobs if j.source_job_id not in seen]
            if not new:
                break  # no new ids -> past the last page
            seen.update(j.source_job_id for j in new)
            jobs.extend(new)
        return jobs

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        tree = HTMLParser(content.decode("utf-8", errors="replace"))
        jobs: list[RawJob] = []
        seen: set[str] = set()
        for anchor in tree.css("a"):
            href = anchor.attributes.get("href") or ""
            path = urllib.parse.urlsplit(href).path
            m = _JOB_PATH_RE.match(path)
            if not m:
                continue
            job_id = m.group(1)
            if job_id in seen:
                continue
            title = _text(anchor)
            if not title:
                continue
            seen.add(job_id)
            jobs.append(
                RawJob(
                    source_job_id=job_id,
                    title=title,
                    url=urllib.parse.urljoin(source_url, href),
                    location=_pick(_closest(anchor, {"li", "article", "div", "tr"}),
                                   ["[class*=location]", ".jobLocation", ".location"]),
                )
            )
        return jobs


def _with_startrow(url: str, startrow: int) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["startrow"] = str(startrow)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def _text(node) -> str:
    return " ".join(node.text(deep=True, separator=" ").split()) if node else ""


def _closest(node, tags: set[str]):
    parent = node.parent
    while parent is not None:
        if parent.tag in tags:
            return parent
        parent = parent.parent
    return node.parent


def _pick(container, selectors: list[str]) -> str | None:
    if container is None:
        return None
    for sel in selectors:
        try:
            found = container.css_first(sel)
        except Exception:
            found = None
        if found:
            txt = _text(found)
            if txt:
                return txt
    return None
