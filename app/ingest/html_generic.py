"""Generic HTML adapter for bespoke careers pages (aut, avondale, divinity).

Entirely config-driven: the CSS selectors live in the university's ``params`` so
a broken selector is a one-line YAML edit, not a code change. This is the
deliberate escape hatch for the long tail that isn't on a known ATS.
"""
from __future__ import annotations

import urllib.parse

from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, parse_date
from app.normalise.core import RawJob


class HtmlGenericAdapter(Adapter):
    name = "html_generic"

    def endpoints(self, university: dict) -> list[dict]:
        return [{"url": university["params"]["listing_url"]}]

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        p = university["params"]
        tree = HTMLParser(content.decode("utf-8", errors="replace"))
        jobs: list[RawJob] = []
        seen: set[str] = set()

        for row in _select_all(tree, p.get("row_selector", "article")):
            link_node = _first(row, p.get("link_selector", "a"))
            title_node = _first(row, p.get("title_selector", "a")) or link_node
            if link_node is None or title_node is None:
                continue
            href = link_node.attributes.get("href")
            title = _text(title_node)
            if not href or not title:
                continue
            url = urllib.parse.urljoin(source_url, href)
            if url in seen:
                continue
            seen.add(url)

            jobs.append(
                RawJob(
                    source_job_id=_source_id(url),
                    title=title,
                    url=url,
                    location=_text(_first(row, p.get("location_selector", ".location"))),
                    posted_at=parse_date(_text(_first(row, p.get("date_selector", "time")))),
                    excerpt=_text(_first(row, p.get("excerpt_selector", "p"))),
                )
            )
        return jobs


def _select_all(tree, selector: str):
    for sel in _split(selector):
        found = tree.css(sel)
        if found:
            return found
    return []


def _first(node, selector: str | None):
    if node is None or not selector:
        return None
    for sel in _split(selector):
        el = node.css_first(sel)
        if el is not None:
            return el
    return None


def _split(selector: str) -> list[str]:
    return [s.strip() for s in selector.split(",") if s.strip()]


def _text(node) -> str | None:
    if node is None:
        return None
    txt = " ".join(node.text(deep=True, separator=" ").split())
    return txt or None


def _source_id(url: str) -> str:
    path = urllib.parse.urlsplit(url).path.rstrip("/")
    return path.rsplit("/", 1)[-1] or url
