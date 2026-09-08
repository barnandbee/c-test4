"""HTTP routes: the board UI, a JSON API, RSS feeds and saved searches, admin."""
from __future__ import annotations

import datetime as dt
import secrets
import urllib.parse
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Depends, Form, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import UNIVERSITY_GROUPS, STATES, get_settings, load_universities
from app.db import get_session
from app.web.auth import COOKIE_NAME, is_admin, make_cookie, require_admin
from app.models import AdapterRun, CrawlRun, Listing, SavedSearch, utcnow
from app.queries import (
    Filters,
    analytics,
    facet_values,
    load_featured,
    load_featured_admin,
    search,
)

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
        "is_admin": is_admin(request),
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
    partial = request.query_params.get("partial") == "1"
    ctx = _template_context(request, session)
    ctx.update({
        "result": result,
        "f": f,
        "selected": _selected(request),
        "query_string": request.url.query,
        "partial": partial,
        # Featured strip only on the full page, and only on the unfiltered default view
        # so it doesn't compete with an active search.
        "featured": [] if (partial or request.url.query) else load_featured(session),
    })
    # Progressive enhancement: return just the results fragment for live filtering.
    template = "results.html" if partial else "index.html"
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


# --- JSON API (admin only) ---------------------------------------------------
@router.get("/api/listings", dependencies=[Depends(require_admin)])
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


# --- admin login -------------------------------------------------------------
@router.get("/admin/login", response_class=HTMLResponse)
def admin_login_form(request: Request):
    ctx = {"request": request, "is_admin": is_admin(request),
           "enabled": bool(get_settings().admin_token),
           "flash": request.query_params.get("flash")}
    return _TEMPLATES.TemplateResponse(request, "login.html", ctx)


@router.post("/admin/login")
def admin_login(request: Request, token: str = Form(...)):
    if get_settings().admin_token and token == get_settings().admin_token:
        resp = RedirectResponse("/admin", status_code=303)
        resp.set_cookie(COOKIE_NAME, make_cookie(), httponly=True, samesite="lax",
                        max_age=get_settings().session_ttl_hours * 3600,
                        secure=request.url.scheme == "https")
        return resp
    return RedirectResponse("/admin/login?flash=bad", status_code=303)


@router.get("/admin/logout")
def admin_logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(COOKIE_NAME)
    return resp


# --- protected API docs ------------------------------------------------------
@router.get("/admin/openapi.json", dependencies=[Depends(require_admin)])
def admin_openapi(request: Request):
    return JSONResponse(request.app.openapi())


@router.get("/admin/api-docs", dependencies=[Depends(require_admin)], response_class=HTMLResponse)
def admin_api_docs():
    return get_swagger_ui_html(openapi_url="/admin/openapi.json", title="AU Uni Jobs API")


# --- admin / health ----------------------------------------------------------
@router.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin(request: Request, session: Session = Depends(get_session)):
    from app.ingest import trigger
    from app.models import SourceResolution

    universities = {u["slug"]: u for u in load_universities()}
    # Latest AdapterRun per university.
    latest: dict[str, AdapterRun] = {}
    for ar in session.scalars(select(AdapterRun).order_by(desc(AdapterRun.started_at))):
        latest.setdefault(ar.university_slug, ar)
    # Auto-discovery results per university (what the ATS actually resolved to).
    resolved: dict[str, SourceResolution] = {
        r.university_slug: r for r in session.scalars(select(SourceResolution))
    }

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
            "resolved": resolved.get(slug),
        })
    last_run = session.scalar(select(CrawlRun).order_by(desc(CrawlRun.started_at)))
    ctx = {
        "request": request, "is_admin": True, "rows": rows, "last_run": last_run, "now": now,
        "trigger": trigger.status(),
        "flash": request.query_params.get("flash"),
    }
    return _TEMPLATES.TemplateResponse(request, "admin.html", ctx)


@router.post("/admin/refresh", dependencies=[Depends(require_admin)])
def admin_refresh(only: str = Form("")):
    """Kick off a full crawl (auto-discovering endpoints) in the background.
    Admin-only (session); the button on /admin posts here."""
    from app.ingest import trigger

    only_list = [s for s in only.replace(",", " ").split() if s] or None
    started = trigger.start(only=only_list)
    return RedirectResponse(
        f"/admin?flash={'started' if started else 'already-running'}", status_code=303
    )


@router.get("/admin/probe", response_class=Response, dependencies=[Depends(require_admin)])
def admin_probe(request: Request, url: str = "", xhr: str = "0"):
    """Fetch a URL from the server and return the raw response head — a ground-truth
    tool for diagnosing adapters from the browser (the host has egress; the build
    sandbox doesn't). Admin-only. e.g. /admin/probe?url=<listing>&xhr=1
    """
    from app.ingest.base import PoliteClient

    if not url:
        return Response("pass ?url=<absolute url>&xhr=0|1", media_type="text/plain")
    headers = ({"X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8"} if xhr == "1" else {})
    try:
        res = PoliteClient().fetch(url, headers=headers)
        body = res.content[:4000].decode("utf-8", errors="replace")
        report = (f"status={res.status_code}\nfinal_url={res.final_url}\n"
                  f"bytes={len(res.content)}\n{'-'*60}\n{body}")
    except Exception as exc:  # noqa: BLE001
        report = f"ERROR fetching {url}\n{type(exc).__name__}: {exc}"
    return Response(report, media_type="text/plain")


@router.get("/admin/analytics", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_analytics(request: Request, session: Session = Depends(get_session)):
    f = filters_from_request(request)
    data = analytics(session, f)
    ctx = {
        "request": request, "is_admin": True, "data": data, "f": f,
        "selected": _selected(request),
        "states": STATES, "role_families": ROLE_FAMILIES, "level_bands": LEVEL_BANDS,
        "posted_windows": POSTED_WINDOWS, "groups": UNIVERSITY_GROUPS,
        "query_string": request.url.query,
    }
    return _TEMPLATES.TemplateResponse(request, "analytics.html", ctx)


# --- featured jobs manager (admin only) --------------------------------------
@router.get("/admin/featured", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_featured(request: Request, q: str = "", session: Session = Depends(get_session)):
    featured = load_featured_admin(session)
    matches = []
    if q:
        res = search(session, Filters(q=q, per_page=15))
        matches = res.listings
    ctx = {
        "request": request, "is_admin": True, "featured": featured, "matches": matches, "q": q,
        "flash": request.query_params.get("flash"),
    }
    return _TEMPLATES.TemplateResponse(request, "featured.html", ctx)


@router.post("/admin/featured/add", dependencies=[Depends(require_admin)])
def admin_featured_add(listing_id: str = Form(...), session: Session = Depends(get_session)):
    from app.models import FeaturedJob
    exists = session.scalar(select(FeaturedJob).where(FeaturedJob.listing_id == listing_id))
    if not exists:
        n = session.scalar(select(func.count()).select_from(FeaturedJob)) or 0
        session.add(FeaturedJob(listing_id=listing_id, position=n + 1))
        session.commit()
    return RedirectResponse("/admin/featured?flash=added", status_code=303)


@router.post("/admin/featured/update", dependencies=[Depends(require_admin)])
def admin_featured_update(fid: int = Form(...), video_url: str = Form(""),
                          headline: str = Form(""), position: int = Form(1),
                          session: Session = Depends(get_session)):
    from app.models import FeaturedJob
    fj = session.get(FeaturedJob, fid)
    if fj:
        fj.video_url = video_url.strip() or None
        fj.headline = headline.strip() or None
        fj.position = position
        session.commit()
    return RedirectResponse("/admin/featured?flash=saved", status_code=303)


@router.post("/admin/featured/remove", dependencies=[Depends(require_admin)])
def admin_featured_remove(fid: int = Form(...), session: Session = Depends(get_session)):
    from app.models import FeaturedJob
    fj = session.get(FeaturedJob, fid)
    if fj:
        session.delete(fj)
        session.commit()
    return RedirectResponse("/admin/featured?flash=removed", status_code=303)


# --- blog (public) -----------------------------------------------------------
@router.get("/blog", response_class=HTMLResponse)
def blog_index(request: Request, session: Session = Depends(get_session)):
    from app.models import BlogPost
    posts = list(session.scalars(
        select(BlogPost).where(BlogPost.status == "published")
        .order_by(desc(BlogPost.published_at))
    ))
    return _TEMPLATES.TemplateResponse(request, "blog_list.html", {
        "request": request, "is_admin": is_admin(request), "posts": posts})


@router.get("/blog/{slug}", response_class=HTMLResponse)
def blog_post(slug: str, request: Request, session: Session = Depends(get_session)):
    from app.models import BlogPost
    post = session.scalar(select(BlogPost).where(BlogPost.slug == slug))
    admin = is_admin(request)
    if not post or (post.status != "published" and not admin):
        return _TEMPLATES.TemplateResponse(request, "blog_missing.html",
                                           {"request": request, "is_admin": admin}, status_code=404)
    return _TEMPLATES.TemplateResponse(request, "blog_post.html",
                                       {"request": request, "is_admin": admin, "post": post})


# --- blog (admin) ------------------------------------------------------------
@router.get("/admin/blog", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_blog(request: Request, session: Session = Depends(get_session)):
    from app.models import BlogPost
    posts = list(session.scalars(select(BlogPost).order_by(desc(BlogPost.updated_at))))
    return _TEMPLATES.TemplateResponse(request, "blog_admin.html", {
        "request": request, "is_admin": True, "posts": posts,
        "flash": request.query_params.get("flash")})


@router.get("/admin/blog/new", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_blog_new(request: Request):
    return _TEMPLATES.TemplateResponse(request, "blog_edit.html",
                                       {"request": request, "is_admin": True, "post": None})


@router.get("/admin/blog/{pid}/edit", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_blog_edit(pid: int, request: Request, session: Session = Depends(get_session)):
    from app.models import BlogPost
    post = session.get(BlogPost, pid)
    if not post:
        return RedirectResponse("/admin/blog", status_code=303)
    return _TEMPLATES.TemplateResponse(request, "blog_edit.html", {
        "request": request, "is_admin": True, "post": post,
        "flash": request.query_params.get("flash")})


@router.post("/admin/blog/save", dependencies=[Depends(require_admin)])
def admin_blog_save(pid: str = Form(""), title: str = Form(...), subtitle: str = Form(""),
                    slug: str = Form(""), body_md: str = Form(""), author: str = Form(""),
                    action: str = Form("save"), session: Session = Depends(get_session)):
    from app.blog import render_markdown, unique_slug
    from app.models import BlogPost
    post = session.get(BlogPost, int(pid)) if pid else None
    if post is None:
        post = BlogPost()
        session.add(post)
    post.title = title.strip()
    post.subtitle = subtitle.strip() or None
    post.author = author.strip() or None
    post.body_md = body_md
    post.body_html = render_markdown(body_md)
    post.slug = unique_slug(session, slug or title, exclude_id=post.id)
    if action == "publish":
        post.status = "published"
        if not post.published_at:
            post.published_at = utcnow()
    elif action == "unpublish":
        post.status = "draft"
    session.commit()
    return RedirectResponse(f"/admin/blog/{post.id}/edit?flash={action}", status_code=303)


@router.post("/admin/blog/delete", dependencies=[Depends(require_admin)])
def admin_blog_delete(pid: int = Form(...), session: Session = Depends(get_session)):
    from app.models import BlogPost
    post = session.get(BlogPost, pid)
    if post:
        session.delete(post)
        session.commit()
    return RedirectResponse("/admin/blog?flash=deleted", status_code=303)


@router.get("/admin/blog/stats-snippet", response_class=Response, dependencies=[Depends(require_admin)])
def admin_blog_stats(session: Session = Depends(get_session)):
    from app.blog import build_stats_markdown
    return Response(build_stats_markdown(session), media_type="text/plain")


def _iso(value: dt.datetime | None) -> str | None:
    return value.isoformat() if value else None
