"""NGA.NET adapter — suspected platform for Western Sydney and QUT.

NGA.NET public boards live at ``{tenant}.nga.net.au`` and render job rows with
links to a job-detail action. Parsing keys off those detail links. Confidence is
low until a live board is confirmed (see DISCOVERY.md); the parser is deliberately
tolerant.
"""
from __future__ import annotations

import re
import urllib.parse

from selectolax.parser import HTMLParser

from app.ingest.base import Adapter, parse_date
from app.normalise.core import RawJob

# NGA.NET detail links look like ...?event=jobs.jobInfo&jobid=<GUID or digits>
# (job ids are usually GUIDs, e.g. DACBA00C-BE1A-4613-9C74-B057010C7FD7).
_JOB_LINK_RE = re.compile(r"(?:jobid=|/job/)([A-Za-z0-9][A-Za-z0-9\-]{3,})", re.I)


class NgaNetAdapter(Adapter):
    name = "nganet"

    def endpoints(self, university: dict) -> list[dict]:
        p = university["params"]
        url = p.get("listing_url") or p.get("base_url")
        return [{"url": url}] if url else []

    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        tree = HTMLParser(content.decode("utf-8", errors="replace"))
        jobs: list[RawJob] = []
        seen: set[str] = set()

        for anchor in tree.css("a"):
            href = anchor.attributes.get("href") or ""
            m = _JOB_LINK_RE.search(href)
            if not m:
                continue
            source_id = m.group(1)
            if source_id in seen:
                continue
            title = _text(anchor)
            if not title:
                continue
            seen.add(source_id)
            row = _closest_row(anchor)
            jobs.append(
                RawJob(
                    source_job_id=source_id,
                    title=title,
                    url=urllib.parse.urljoin(source_url, href),
                    location=_cell(row, ["location", "region"]),
                    classification_raw=_cell(row, ["classification", "category"]),
                    work_type_raw=_cell(row, ["work-type", "employment", "type"]),
                    closes_at=parse_date(_cell(row, ["closing", "close"])),
                )
            )
        return jobs


def _text(node) -> str | None:
    if node is None:
        return None
    return " ".join(node.text(deep=True, separator=" ").split()) or None


def _closest_row(node):
    parent = node.parent
    while parent is not None:
        if parent.tag in {"tr", "li", "article", "div"}:
            return parent
        parent = parent.parent
    return node.parent


def _cell(row, hints: list[str]) -> str | None:
    if row is None:
        return None
    for hint in hints:
        el = row.css_first(f"[class*={hint}]")
        if el:
            txt = _text(el)
            if txt:
                return txt
    return None
