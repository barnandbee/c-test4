"""Search + faceting over listings. Postgres tsvector FTS, everything else plain
SQL filters. This is all the search machinery a few-thousand-row dataset needs.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.config import load_universities
from app.models import Listing


def _group_map() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for uni in load_universities():
        for g in uni.get("groups") or []:
            groups.setdefault(g, []).append(uni["slug"])
    return groups


@dataclass
class Filters:
    q: str | None = None
    universities: list[str] = field(default_factory=list)
    groups: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    cities: list[str] = field(default_factory=list)
    role_families: list[str] = field(default_factory=list)
    level_bands: list[str] = field(default_factory=list)
    classification: str | None = None
    work_types: list[str] = field(default_factory=list)
    time_fractions: list[str] = field(default_factory=list)
    disciplines: list[str] = field(default_factory=list)
    salary_min: float | None = None
    salary_max: float | None = None
    has_salary: bool = False
    remote: bool = False
    posted_within: str | None = None   # "24h" | "7d" | "30d"
    closing_soon: bool = False         # closes within 7 days
    status: str = "open"
    sort: str = "posted"               # posted | closing | salary
    page: int = 1
    per_page: int = 50


def _apply(stmt: Select, f: Filters) -> Select:
    stmt = stmt.where(Listing.status == f.status)

    # University selection: explicit slugs UNION group presets.
    slugs = set(f.universities)
    if f.groups:
        gm = _group_map()
        for g in f.groups:
            slugs.update(gm.get(g, []))
    if slugs:
        stmt = stmt.where(Listing.university_slug.in_(slugs))

    if f.states:
        stmt = stmt.where(Listing.state.in_(f.states))
    if f.cities:
        # campus_location is free text; match any selected city as a substring.
        stmt = stmt.where(or_(*[Listing.campus_location.ilike(f"%{c}%") for c in f.cities]))
    if f.role_families:
        stmt = stmt.where(Listing.role_family.in_(f.role_families))
    if f.level_bands:
        stmt = stmt.where(Listing.level_band.in_(f.level_bands))
    if f.classification:
        stmt = stmt.where(Listing.classification_raw.ilike(f"%{f.classification}%"))
    if f.work_types:
        stmt = stmt.where(Listing.work_type.in_(f.work_types))
    if f.time_fractions:
        stmt = stmt.where(Listing.time_fraction.in_(f.time_fractions))
    if f.disciplines:
        stmt = stmt.where(Listing.discipline.in_(f.disciplines))
    if f.remote:
        stmt = stmt.where(Listing.remote_flag.is_(True))

    if f.has_salary:
        stmt = stmt.where(Listing.salary_min.is_not(None))
    if f.salary_min is not None:
        # keep rows whose max is at least the requested floor (or unknown salary
        # only if has_salary not requested)
        stmt = stmt.where(Listing.salary_max >= f.salary_min)
    if f.salary_max is not None:
        stmt = stmt.where(Listing.salary_min <= f.salary_max)

    now = dt.datetime.now(dt.timezone.utc)
    if f.posted_within:
        delta = {"24h": dt.timedelta(hours=24), "7d": dt.timedelta(days=7),
                 "30d": dt.timedelta(days=30)}.get(f.posted_within)
        if delta:
            stmt = stmt.where(Listing.posted_at >= now - delta)
    if f.closing_soon:
        stmt = stmt.where(Listing.closes_at.is_not(None),
                          Listing.closes_at >= now,
                          Listing.closes_at <= now + dt.timedelta(days=7))

    if f.q:
        # tsvector full-text match across the generated search_vector, plus a
        # trigram-ish ILIKE safety net for short/partial words.
        ts = func.plainto_tsquery("english", f.q)
        stmt = stmt.where(
            or_(Listing.search_vector.op("@@")(ts),
                Listing.title.ilike(f"%{f.q}%"))
        )
    return stmt


def _order(stmt: Select, f: Filters) -> Select:
    if f.sort == "closing":
        return stmt.order_by(Listing.closes_at.is_(None), Listing.closes_at.asc())
    if f.sort == "salary":
        return stmt.order_by(Listing.salary_max.is_(None), Listing.salary_max.desc())
    return stmt.order_by(Listing.posted_at.is_(None), Listing.posted_at.desc())


@dataclass
class SearchResult:
    listings: list[Listing]
    total: int
    page: int
    per_page: int
    facets: dict


def search(session: Session, f: Filters) -> SearchResult:
    base = _apply(select(Listing), f)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0

    stmt = _order(base, f).limit(f.per_page).offset((f.page - 1) * f.per_page)
    listings = list(session.scalars(stmt))

    return SearchResult(
        listings=listings, total=total, page=f.page, per_page=f.per_page,
        facets=_facets(session, f),
    )


def _facets(session: Session, f: Filters) -> dict:
    """Counts for the honest UI — e.g. how many listings actually have a salary."""
    open_only = select(Listing).where(Listing.status == "open")
    total_open = session.scalar(select(func.count()).select_from(open_only.subquery())) or 0
    with_salary = session.scalar(
        select(func.count()).select_from(
            open_only.where(Listing.salary_min.is_not(None)).subquery()
        )
    ) or 0
    return {
        "total_open": total_open,
        "with_salary": with_salary,
        "salary_pct": round(100 * with_salary / total_open) if total_open else 0,
    }


def facet_values(session: Session) -> dict:
    """Distinct values for populating filter controls (cheap at this scale)."""
    def distinct(col):
        return [v for (v,) in session.execute(
            select(col).where(col.is_not(None), Listing.status == "open").distinct().order_by(col)
        )]
    return {
        "disciplines": distinct(Listing.discipline),
        "cities": distinct(Listing.campus_location),
        "role_families": distinct(Listing.role_family),
        "level_bands": distinct(Listing.level_band),
        "work_types": distinct(Listing.work_type),
    }
