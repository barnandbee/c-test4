"""PageUp People adapter — the dominant ATS in Australian HE (~30 of 42 unis).

PageUp "careers website" (cw) instances expose:
  * an optional RSS feed (preferred: cheap, structured)
  * an HTML listing page (fallback)

Both are parsed into RawJobs. The HTML parser keys off PageUp's stable job-URL
pattern (``/job/<id>/<slug>``) rather than brittle wrapper classes, then reads
whatever meta it can find in the surrounding container.
"""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

import httpx
from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, NotModified, PoliteClient, parse_date
from app.normalise.core import RawJob

_JOB_URL_RE = re.compile(r"/job/(\d+)")
_MAX_PAGES = 40   # generous cap; no AU uni lists this many pages
_XHR_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
}


class PageUpAdapter(Adapter):
    name = "pageup"

    def endpoints(self, university: dict) -> list[dict]:
        params = university["params"]
        specs: list[dict] = []
        # Preference order: feed first, then HTML listing.
        if params.get("feed_url"):
            specs.append({"url": params["feed_url"]})
        if params.get("listing_url"):
            specs.append({"url": params["listing_url"]})
        return specs

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        """Fetch the full listing.

        The HTML listing is paginated (``?page=N``); we walk every page and dedupe
        by job id so the board reflects *all* live vacancies, not just page one.
        A feed (if configured) is tried first as it's the cheapest complete source.
        """
        params = university["params"]

        if params.get("feed_url"):
            result = client.fetch(params["feed_url"])
            if result.not_modified:
                raise NotModified(params["feed_url"])
            jobs = self.parse(result.content, university, params["feed_url"])
            if jobs:
                return jobs  # feeds carry the recent set; good enough when present

        listing_url = params.get("listing_url")
        if not listing_url:
            return []
        # Tolerate the /cw/ vs /caw/ path variant when discovery didn't fix it.
        for candidate in _path_variants(listing_url):
            try:
                jobs = self._paginate(client, university, candidate)
            except httpx.HTTPStatusError:
                continue
            if jobs:
                return jobs
        return []

    def _paginate(self, client: PoliteClient, university: dict, listing_url: str) -> list[RawJob]:
        seen: set[str] = set()
        jobs: list[RawJob] = []

        # Decide the fetch mode on page 1. PageUp's default listing is
        # server-rendered HTML, so try a plain GET first. Only if that yields
        # nothing fall back to the XHR/JSON API (for genuine JS-shell tenants):
        # sending XHR headers to a server-rendered tenant flips its response to a
        # JSON body our fragment parser can't read, which silently zeroed out
        # otherwise-healthy boards.
        url1 = _with_page(listing_url, 1)
        result = client.fetch(url1)
        if result.not_modified:
            raise NotModified(listing_url)
        page_jobs = self.parse(result.content, university, url1)
        page_headers: dict | None = None
        if not page_jobs:
            # Drop any validator the plain request cached so the retry isn't
            # answered with a 304 for the wrong representation.
            cache = getattr(client, "_cache", None)
            if isinstance(cache, dict):
                cache.pop(url1, None)
            result = client.fetch(url1, headers=_XHR_HEADERS)
            if result.not_modified:
                raise NotModified(listing_url)
            page_jobs = self.parse(result.content, university, url1)
            page_headers = _XHR_HEADERS

        for j in page_jobs:
            if j.source_job_id not in seen:
                seen.add(j.source_job_id)
                jobs.append(j)
        if not jobs:
            return []

        # Walk the remaining pages using whichever mode worked on page 1.
        for page in range(2, _MAX_PAGES + 1):
            url = _with_page(listing_url, page)
            result = client.fetch(url, headers=page_headers or {})
            if result.not_modified:
                break
            page_jobs = self.parse(result.content, university, url)
            new = [j for j in page_jobs if j.source_job_id not in seen]
            if not new:
                break  # no new ids -> past the last page
            seen.update(j.source_job_id for j in new)
            jobs.extend(new)
        return jobs

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        text = content.decode("utf-8", errors="replace").lstrip()
        if text.startswith("<?xml") or "<rss" in text[:200].lower():
            return self._parse_rss(text, university, source_url)
        if text[:1] in "{[":
            # PageUp XHR responses are JSON, usually wrapping an HTML fragment.
            # Extract any HTML that contains job links and parse it — schema-agnostic.
            html = _html_from_json(text)
            if html:
                return self._parse_html(html, university, source_url)
            return []
        return self._parse_html(text, university, source_url)

    # ---- RSS ---------------------------------------------------------------
    def _parse_rss(self, text: str, university: dict, source_url: str) -> list[RawJob]:
        jobs: list[RawJob] = []
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return jobs
        for item in root.iter("item"):
            link = _t(item.find("link"))
            title = _t(item.find("title"))
            if not link or not title:
                continue
            m = _JOB_URL_RE.search(link)
            source_id = m.group(1) if m else link
            desc = _t(item.find("description")) or ""
            jobs.append(
                RawJob(
                    source_job_id=source_id,
                    title=title,
                    url=link,
                    posted_at=parse_date(_t(item.find("pubDate"))),
                    excerpt=_strip_html(desc),
                    classification_raw=_category(item),
                )
            )
        return jobs

    # ---- HTML --------------------------------------------------------------
    def _parse_html(self, html: str, university: dict, source_url: str) -> list[RawJob]:
        tree = HTMLParser(html)
        jobs: list[RawJob] = []
        seen: set[str] = set()

        for anchor in tree.css("a"):
            href = anchor.attributes.get("href") or ""
            m = _JOB_URL_RE.search(href)
            if not m:
                continue
            source_id = m.group(1)
            if source_id in seen:
                continue
            seen.add(source_id)

            title = _text(anchor)
            if not title:
                continue
            url = urllib.parse.urljoin(source_url, href)
            container = _closest(anchor, {"article", "li", "div"})

            jobs.append(
                RawJob(
                    source_job_id=source_id,
                    title=title,
                    url=url,
                    location=_pick(container, [".location", ".job-location", "[class*=location]"]),
                    classification_raw=_pick(
                        container, [".categories", ".classification", ".job-categories"]
                    ),
                    work_type_raw=_pick(container, [".work-type", ".worktype", "[class*=work-type]"]),
                    posted_at=parse_date(_pick_attr(container, "time", "datetime")
                                         or _pick(container, [".date", ".posted", "time"])),
                    closes_at=parse_date(_pick(container, [".closing", ".closes", ".close-date"])),
                    salary_raw=_pick(container, [".salary", "[class*=salary]"]),
                    excerpt=_pick(container, [
                        ".job-description-summary", ".summary", ".job-summary", "p"
                    ]),
                )
            )
        return jobs


# --- JSON handling -----------------------------------------------------------
def _html_from_json(text: str) -> str:
    """Pull the job-bearing HTML out of a PageUp XHR JSON response.

    PageUp wraps the listing HTML in JSON (field names vary by tenant/version), so
    rather than hard-code keys we recursively collect every string value that
    looks like it contains job markup and concatenate them. Schema-agnostic: works
    whether the fragment lives under `results`, `SearchResults`, `content`, etc.
    """
    import json as _json

    try:
        data = _json.loads(text)
    except _json.JSONDecodeError:
        return ""
    fragments: list[str] = []

    def walk(node):
        if isinstance(node, str):
            if "/job/" in node and "<" in node:
                fragments.append(node)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return "\n".join(fragments)


# --- pagination helpers ------------------------------------------------------
def _with_page(url: str, page: int) -> str:
    """Add/replace a ?page=N query param on a PageUp listing URL."""
    parts = urllib.parse.urlsplit(url)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["page"] = str(page)
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), parts.fragment)
    )


def _path_variants(url: str) -> list[str]:
    """PageUp listing paths appear as both /cw/ and /caw/; try the given one first."""
    variants = [url]
    if "/caw/" in url:
        variants.append(url.replace("/caw/", "/cw/"))
    elif "/cw/" in url:
        variants.append(url.replace("/cw/", "/caw/"))
    return variants


# --- small DOM helpers -------------------------------------------------------
def _t(el) -> str | None:
    return el.text.strip() if el is not None and el.text else None


def _text(node) -> str:
    return " ".join(node.text(deep=True, separator=" ").split()) if node else ""


def _category(item) -> str | None:
    cat = item.find("category")
    return _t(cat)


def _strip_html(value: str) -> str:
    return " ".join(HTMLParser(value).text(separator=" ").split()) if value else ""


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


def _pick_attr(container, selector: str, attr: str) -> str | None:
    if container is None:
        return None
    found = container.css_first(selector)
    if found:
        return found.attributes.get(attr)
    return None
