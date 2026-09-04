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

from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, parse_date
from app.normalise.core import RawJob

_JOB_URL_RE = re.compile(r"/job/(\d+)")


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

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        text = content.decode("utf-8", errors="replace").lstrip()
        if text.startswith("<?xml") or "<rss" in text[:200].lower():
            return self._parse_rss(text, university, source_url)
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
