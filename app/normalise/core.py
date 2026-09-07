"""Core normalisation: merge an adapter's RawJob with its university context and
derive every computed field of the shared schema.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
from typing import Any

from app.normalise.classify import (
    classify_discipline,
    classify_role_family,
    parse_remote,
    parse_salary,
    parse_time_fraction,
    parse_work_type,
)
from app.normalise.levels import classify_level


@dataclasses.dataclass
class RawJob:
    """The neutral shape every adapter must emit. Adapter-specific parsing ends
    here; everything downstream is platform-agnostic."""

    source_job_id: str
    title: str
    url: str
    location: str | None = None
    posted_at: dt.datetime | None = None
    closes_at: dt.datetime | None = None
    classification_raw: str | None = None
    work_type_raw: str | None = None
    time_fraction_raw: str | None = None
    salary_raw: str | None = None
    excerpt: str | None = None
    remote: bool | None = None


def make_id(university_slug: str, source_job_id: str) -> str:
    """Stable content-addressable id. Same (uni, job ref) -> same id across runs,
    so upserts are idempotent and cross-run diffing is trivial."""
    digest = hashlib.sha256(f"{university_slug}::{source_job_id}".encode()).hexdigest()
    return digest[:64]


def _clean_excerpt(text: str | None, limit: int = 300) -> str | None:
    """Store only a short snippet — never republish full descriptions (CRAWLING.md)."""
    if not text:
        return None
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rsplit(" ", 1)[0] + "…"


def normalise_record(raw: RawJob, university: dict[str, Any]) -> dict[str, Any]:
    """Produce a fully-normalised record dict ready to become a Listing/Staging row."""
    slug = university["slug"]

    level = classify_level(raw.classification_raw, raw.title)
    role_family = classify_role_family(raw.title, raw.classification_raw, raw.excerpt)
    discipline = classify_discipline(raw.title, raw.excerpt, role_family)

    work_type = parse_work_type(raw.work_type_raw, raw.classification_raw, raw.excerpt, raw.title)
    time_fraction = (
        parse_time_fraction(raw.time_fraction_raw, raw.work_type_raw, raw.excerpt, raw.title)
    )
    remote = raw.remote if raw.remote is not None else parse_remote(raw.location, raw.excerpt, raw.title)
    salary = parse_salary(raw.salary_raw, raw.classification_raw, raw.excerpt)

    return {
        "id": make_id(slug, raw.source_job_id),
        "source_job_id": raw.source_job_id,
        "university": university["name"],
        "university_slug": slug,
        "state": university["state"],
        "campus_location": raw.location,
        "title": raw.title.strip(),
        "url": raw.url,
        "posted_at": raw.posted_at,
        "closes_at": raw.closes_at,
        "work_type": work_type,
        "time_fraction": time_fraction,
        "role_family": role_family,
        "classification_raw": raw.classification_raw,
        "level_band": level["level_band"],
        "level_scale": level["level_scale"],
        # Canonical pay grade (HEW n / Academic Level X). Often the only salary
        # signal a listing gives, so surface it as its own category.
        "pay_grade": level["level_label"] or "Not specified",
        "salary_min": salary["salary_min"],
        "salary_max": salary["salary_max"],
        "salary_raw": salary["salary_raw"],
        "discipline": discipline,
        "remote_flag": bool(remote),
        "excerpt": _clean_excerpt(raw.excerpt),
    }
