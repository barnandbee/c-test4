"""Clinch adapter — PageUp's candidate-facing product (clinchtalent.com /
career-pages.com).

Several Australian universities migrated off classic PageUp onto Clinch; the old
``careers.pageuppeople.com/<id>`` URLs now 301-redirect to a Clinch site served on
a vanity host (e.g. ``external.jobs.uwa.edu.au``). Clinch server-renders job cards
that link to ``/jobs/<slug>``, paginated with ``?page=N`` — no XHR needed.

Some Clinch tenants sit behind an AWS WAF anti-bot challenge (a CAPTCHA or a JS
proof-of-work page). We do **not** defeat bot protection (CRAWLING.md), so a
challenge page is reported as an explicit ``WafChallenge`` block, never scraped.
"""
from __future__ import annotations

import re
import urllib.parse

from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, NotModified, PoliteClient, WafChallenge
from app.normalise.core import RawJob

# Clinch job links are /jobs/<slug>; exclude the /jobs/search listing itself.
_JOB_PATH_RE = re.compile(r"^/jobs/(?!search$)([a-z0-9][a-z0-9\-]+)$", re.I)
_MAX_PAGES = 40
_WAF_MARKERS = ("awswaf", "gokuProps", "awsWafCookieDomainList", "Human Verification")


class ClinchAdapter(Adapter):
    name = "clinch"

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
        for page in range(1, _MAX_PAGES + 1):
            url = _with_page(base, page)
            result = client.fetch(url)
            if result.not_modified:
                if page == 1:
                    raise NotModified(base)
                break
            _guard_waf(result.content, result.final_url or url)
            page_jobs = self.parse(result.content, university, result.final_url or url)
            new = [j for j in page_jobs if j.source_job_id not in seen]
            if not new:
                break  # no new slugs -> past the last page
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
            slug = m.group(1)
            if slug in seen:
                continue
            title = _text(anchor)
            if not title:
                continue  # image/other anchor to same job — the titled one follows
            seen.add(slug)
            # Campus location isn't reliably recoverable from the slug (it blends
            # with the title), so leave it to the university's configured state.
            jobs.append(
                RawJob(
                    source_job_id=slug,
                    title=title,
                    url=urllib.parse.urljoin(source_url, href),
                )
            )
        return jobs


def _guard_waf(content: bytes, url: str) -> None:
    head = content[:2000].decode("utf-8", errors="replace")
    if any(marker in head for marker in _WAF_MARKERS):
        raise WafChallenge(f"AWS WAF anti-bot challenge at {url}; not bypassed")


def _with_page(url: str, page: int) -> str:
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["page"] = str(page)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def _text(node) -> str:
    return " ".join(node.text(deep=True, separator=" ").split()) if node else ""
