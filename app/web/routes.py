"""HTTP routes: the board UI, a JSON API, RSS feeds and saved searches, admin."""
from __future__ import annotations

import datetime as dt
import secrets
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import UNIVERSITY_GROUPS, STATES, load_universities
from app.db import get_session
from app.models import AdapterRun, CrawlRun, Listing, SavedSearch, utcnow
from app.queries import Filters, facet_values, search

router = APIRouter()
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))


# --- template filters --------------------------------------------------------
def _ensure_aware(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo else value.replace(tzinfo=dt.timezone.utc)


def ago(value: dt.datetime | None) -> str:
    if not value:
        return "—"
    days = (dt.datetime.now(dt.timezone.utc) - _ensure_aware(value)).days
    if days <= 0:
        return "today"
    if days == 1:
        return "1 day ago"
    if days < 14:
        return f"{days} days ago"
    if days < 60:
        return f"{days // 7} weeks ago"
    return f"{days // 30} months ago"


def until(value: dt.datetime | None) -> str:
    if not value:
        return "no close date"
    days = (_ensure_aware(value) - dt.datetime.now(dt.timezone.utc)).days
    if days < 0:
        return "closed"
    if days == 0:
        return "closes today"
    if days == 1:
        return "closes tomorrow"
    return f"closes in {days} days"


def money(value: float | None) -> str:
    return f"${value:,.0f}" if value else ""


def salary_range(l) -> str:
    if not l.salary_min:
        return ""
    if l.salary_max and l.salary_max != l.salary_min:
        return f"{money(l.salary_min)}–{money(l.salary_max)}"
    return money(l.salary_min)


_TEMPLATES.env.filters["ago"] = ago
_TEMPLATES.env.filters["until"] = until
_TEMPLATES.env.filters["money"] = money
_TEMPLATES.env.globals["salary_range"] = salary_range

WORK_TYPES = ["continuing", "fixed-term", "casual", "contract"]
TIME_FRACTIONS = ["full-time", "part-time"]
ROLE_FAMILIES = ["academic", "research", "teaching-only", "professional",
                 "technical", "executive", "student"]
LEVEL_BANDS = ["entry", "early-career", "mid", "senior", "leadership"]
POSTED_WINDOWS = [("24h", "Last 24 hours"), ("7d", "Last 7 days"), ("30d", "Last 30 days")]


# --- filter parsing ----------------------------------------------------------
def filters_from_request(request: Request) -> Filters:
    qp = request.query_params
    def many(key: str) -> list[str]:
        return [v for v in qp.getlist(key) if v]
    def num(key: str) -> float | None:
        val = qp.get(key)
        try:
            return float(val) if val not in (None, "") else None
        except ValueError:
            return None
    try:
        page = max(1, int(qp.get("page", "1")))
    except ValueError:
        page = 1
    return Filters(
        q=qp.get("q") or None,
        universities=many("university"),
        groups=many("group"),
        states=many("state"),
        cities=many("city"),
        role_families=many("role_family"),
        level_bands=many("level_band"),
        classification=qp.get("classification") or None,
        work_types=many("work_type"),
        time_fractions=many("time_fraction"),
        disciplines=many("discipline"),
        salary_min=num("salary_min"),
        salary_max=num("salary_max"),
        has_salary=qp.get("has_salary") == "1",
        remote=qp.get("remote") == "1",
        posted_within=qp.get("posted_within") or None,
        closing_soon=qp.get("closing_soon") == "1",
        sort=qp.get("sort") or "posted",
        page=page,
    )


def _template_context(request: Request, session: Session) -> dict:
    fv = facet_values(session)
    universities = load_universities()
    return {
        "request": request,
        "universities": sorted(universities, key=lambda u: u["name"]),
        "groups": UNIVERSITY_GROUPS,
        "states": STATES,
        "work_types": WORK_TYPES,
        "time_fractions": TIME_FRACTIONS,
        "role_families": ROLE_FAMILIES,
        "level_bands": LEVEL_BANDS,
        "posted_windows": POSTED_WINDOWS,
        "facet_values": fv,
        "now": dt.datetime.now(dt.timezone.utc),
    }


# --- board -------------------------------------------------------------------
@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    f = filters_from_request(request)
    result = search(session, f)
    ctx = _template_context(request, session)
    ctx.update({
        "result": result,
        "f": f,
        "selected": _selected(request),
        "query_string": request.url.query,
        "partial": request.query_params.get("partial") == "1",
    })
    # Progressive enhancement: return just the results fragment for live filtering.
    template = "results.html" if ctx["partial"] else "index.html"
    return _TEMPLATES.TemplateResponse(request, template, ctx)


def _selected(request: Request) -> dict:
    """Map of currently-selected multi-values for checkbox state in the template."""
    qp = request.query_params
    keys = ["university", "group", "state", "city", "role_family", "level_band",
            "work_type", "time_fraction", "discipline"]
    sel = {k: set(qp.getlist(k)) for k in keys}
    sel["scalar"] = {
        "posted_within": qp.get("posted_within", ""),
        "sort": qp.get("sort", "posted"),
        "q": qp.get("q", ""),
        "classification": qp.get("classification", ""),
        "salary_min": qp.get("salary_min", ""),
        "salary_max": qp.get("salary_max", ""),
        "has_salary": qp.get("has_salary") == "1",
        "remote": qp.get("remote") == "1",
        "closing_soon": qp.get("closing_soon") == "1",
    }
    return sel


# --- JSON API ----------------------------------------------------------------
@router.get("/api/listings")
def api_listings(request: Request, session: Session = Depends(get_session)):
    f = filters_from_request(request)
    result = search(session, f)
    return JSONResponse({
        "total": result.total,
        "page": result.page,
        "per_page": result.per_page,
        "facets": result.facets,
        "listings": [
            {
                "id": l.id, "title": l.title, "university": l.university,
                "state": l.state, "campus_location": l.campus_location,
                "url": l.url, "posted_at": _iso(l.posted_at), "closes_at": _iso(l.closes_at),
                "work_type": l.work_type, "time_fraction": l.time_fraction,
                "role_family": l.role_family, "classification_raw": l.classification_raw,
                "level_band": l.level_band, "salary_min": l.salary_min,
                "salary_max": l.salary_max, "discipline": l.discipline,
                "remote_flag": l.remote_flag, "excerpt": l.excerpt,
            }
            for l in result.listings
        ],
    })


# --- saved searches + RSS ----------------------------------------------------
@router.post("/saved-searches")
def create_saved_search(request: Request, name: str = Form(...),
                        query_string: str = Form(""),
                        session: Session = Depends(get_session)):
    token = secrets.token_urlsafe(12)
    session.add(SavedSearch(token=token, name=name or "Saved search",
                            query_string=query_string))
    session.commit()
    return RedirectResponse(url=f"/s/{token}", status_code=303)


@router.get("/s/{token}", response_class=HTMLResponse)
def open_saved_search(token: str, request: Request, session: Session = Depends(get_session)):
    ss = session.scalar(select(SavedSearch).where(SavedSearch.token == token))
    if not ss:
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/?{ss.query_string}", status_code=303)


@router.get("/rss")
def rss(request: Request, session: Session = Depends(get_session)):
    """RSS feed of the current filter set (the same query params as the board)."""
    f = filters_from_request(request)
    f.per_page = 100
    result = search(session, f)
    base = str(request.base_url).rstrip("/")
    self_url = f"{base}/rss?{request.url.query}"
    items = "\n".join(_rss_item(l) for l in result.listings)
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>Australian University Jobs — filtered feed</title>
<link>{escape(base)}/?{escape(request.url.query)}</link>
<description>{result.total} matching vacancies</description>
<atom:link xmlns:atom="http://www.w3.org/2005/Atom" href="{escape(self_url)}" rel="self" type="application/rss+xml"/>
{items}
</channel></rss>"""
    return Response(content=body, media_type="application/rss+xml")


def _rss_item(l: Listing) -> str:
    desc = l.excerpt or ""
    meta = " · ".join(filter(None, [l.university, l.campus_location, l.classification_raw]))
    pub = l.posted_at.strftime("%a, %d %b %Y %H:%M:%S %z") if l.posted_at else ""
    return (f"<item><title>{escape(l.title)} — {escape(l.university)}</title>"
            f"<link>{escape(l.url)}</link><guid isPermaLink='false'>{l.id}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{escape(meta + ' — ' + desc)}</description></item>")


# --- admin / health ----------------------------------------------------------
@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request, session: Session = Depends(get_session)):
    universities = {u["slug"]: u for u in load_universities()}
    # Latest AdapterRun per university.
    latest: dict[str, AdapterRun] = {}
    for ar in session.scalars(select(AdapterRun).order_by(desc(AdapterRun.started_at))):
        latest.setdefault(ar.university_slug, ar)

    now = utcnow()
    rows = []
    for slug, uni in sorted(universities.items(), key=lambda kv: kv[1]["name"]):
        ar = latest.get(slug)
        stale = True
        if ar and ar.started_at:
            stale = (now - ar.started_at) > dt.timedelta(hours=36)
        rows.append({
            "uni": uni, "slug": slug, "run": ar,
            "stale": stale,
            "confidence": uni.get("confidence", "?"),
        })
    last_run = session.scalar(select(CrawlRun).order_by(desc(CrawlRun.started_at)))
    ctx = {"request": request, "rows": rows, "last_run": last_run, "now": now}
    return _TEMPLATES.TemplateResponse(request, "admin.html", ctx)


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None
