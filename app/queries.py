"""Search + faceting over listings. Postgres tsvector FTS, everything else plain
SQL filters. This is all the search machinery a few-thousand-row dataset needs.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.config import load_universities
from app.models import FeaturedJob, Listing


def load_featured(session: Session, limit: int = 3) -> list[dict]:
    """Return up to `limit` featured jobs (open only), joined to their listing,
    ordered by position. Each dict = {featured, listing}."""
    rows = session.execute(
        select(FeaturedJob, Listing)
        .join(Listing, Listing.id == FeaturedJob.listing_id)
        .where(Listing.status == "open")
        .order_by(FeaturedJob.position, FeaturedJob.created_at)
        .limit(limit)
    ).all()
    return [{"featured": f, "listing": l} for (f, l) in rows]


def load_featured_admin(session: Session) -> list[dict]:
    """All featured (incl. stale/closed) with their listing if it still exists —
    for the admin manager. Left join so a featured row survives listing churn."""
    rows = session.execute(
        select(FeaturedJob, Listing)
        .join(Listing, Listing.id == FeaturedJob.listing_id, isouter=True)
        .order_by(FeaturedJob.position, FeaturedJob.created_at)
    ).all()
    return [{"featured": f, "listing": l} for (f, l) in rows]


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
    skills: list[str] = field(default_factory=list)
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
    if f.skills:
        stmt = stmt.where(Listing.skills.overlap(f.skills))  # any selected skill present
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


_BAND_ORDER = {b: i for i, b in enumerate(
    ["entry", "early-career", "mid", "senior", "leadership"])}

# Very common words to drop from the title word cloud (structural, not insightful).
_TITLE_STOPWORDS = {
    "and", "the", "for", "with", "of", "in", "to", "at", "on", "a", "an", "or",
    "amp", "level", "band", "part", "full", "time", "term", "fixed", "ongoing",
    "continuing", "casual", "position", "role", "opportunity", "opportunities",
    "job", "jobs", "vacancy", "vacancies", "multiple", "various", "x", "new",
}


def title_wordcloud(titles: list[str], limit: int = 45) -> list[tuple[str, int]]:
    """Frequency of meaningful words across job titles (for the analytics cloud)."""
    import re
    from collections import Counter
    counts: Counter = Counter()
    for t in titles:
        for w in re.findall(r"[a-zA-Z][a-zA-Z\-']+", (t or "").lower()):
            w = w.strip("-'")
            if len(w) >= 3 and w not in _TITLE_STOPWORDS:
                counts[w] += 1
    return counts.most_common(limit)


def analytics(session: Session, f: Filters) -> dict:
    """Aggregate the filtered listing set across every dimension, for the dashboard.
    Respects the same Filters as the board, so you can analyse any subset."""
    base = _apply(select(Listing), f).subquery()
    now = dt.datetime.now(dt.timezone.utc)

    def total(*conds) -> int:
        stmt = select(func.count()).select_from(base)
        for c in conds:
            stmt = stmt.where(c)
        return session.scalar(stmt) or 0

    def by(col, limit: int | None = None, order: str = "count") -> dict:
        stmt = (select(col, func.count()).select_from(base)
                .where(col.is_not(None)).group_by(col))
        stmt = stmt.order_by(func.count().desc()) if order == "count" else stmt.order_by(col)
        rows = [(v, c) for v, c in session.execute(stmt).all()]
        if order == "band":
            rows.sort(key=lambda r: _BAND_ORDER.get(r[0], 99))
        if limit:
            rows = rows[:limit]
        return {"rows": rows, "max": max((c for _, c in rows), default=0)}

    c = base.c
    n = total()
    with_salary = total(c.salary_min.is_not(None))

    # Word cloud of meaningful words in the (filtered) job titles.
    titles = [t for (t,) in session.execute(select(c.title))]
    wordcloud = title_wordcloud(titles)

    # Skills map: unnest the skill tags across the filtered set and count.
    from app.normalise.skills import skill_category
    un = select(func.unnest(c.skills).label("skill")).subquery()
    skill_rows = [(s, ct) for s, ct in session.execute(
        select(un.c.skill, func.count()).group_by(un.c.skill).order_by(func.count().desc())).all()]
    top_skills = {"rows": skill_rows[:20], "max": max((ct for _, ct in skill_rows), default=0)}
    skills_map: dict[str, list] = {}
    for skill, ct in skill_rows:
        skills_map.setdefault(skill_category(skill), []).append((skill, ct))

    return {
        "wordcloud": wordcloud,
        "top_skills": top_skills,
        "skills_map": skills_map,
        "total": n,
        "summary": {
            "with_salary": with_salary,
            "salary_pct": round(100 * with_salary / n) if n else 0,
            "remote": total(c.remote_flag.is_(True)),
            "posted_7d": total(c.posted_at >= now - dt.timedelta(days=7)),
            "posted_30d": total(c.posted_at >= now - dt.timedelta(days=30)),
            "closing_7d": total(c.closes_at.is_not(None), c.closes_at >= now,
                                c.closes_at <= now + dt.timedelta(days=7)),
            "avg_salary_min": session.scalar(select(func.avg(c.salary_min)).select_from(base)),
            "avg_salary_max": session.scalar(select(func.avg(c.salary_max)).select_from(base)),
        },
        "by_state": by(c.state),
        "by_role_family": by(c.role_family),
        "by_level_band": by(c.level_band, order="band"),
        "by_work_type": by(c.work_type),
        "by_time_fraction": by(c.time_fraction),
        "by_university": by(c.university, limit=12),
        "by_discipline": by(c.discipline, limit=12),
    }


def facet_values(session: Session) -> dict:
    """Distinct values for populating filter controls (cheap at this scale)."""
    def distinct(col):
        return [v for (v,) in session.execute(
            select(col).where(col.is_not(None), Listing.status == "open").distinct().order_by(col)
        )]
    un = select(func.unnest(Listing.skills).label("skill")).where(
        Listing.status == "open").subquery()
    skills = [s for (s, _c) in session.execute(
        select(un.c.skill, func.count()).group_by(un.c.skill)
        .order_by(func.count().desc()).limit(40))]
    return {
        "disciplines": distinct(Listing.discipline),
        "cities": distinct(Listing.campus_location),
        "role_families": distinct(Listing.role_family),
        "level_bands": distinct(Listing.level_band),
        "work_types": distinct(Listing.work_type),
        "skills": skills,
    }
