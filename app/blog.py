"""Blog helpers: slugging, Markdown rendering, and the live-stats snapshot that
admins can drop into a post (the 'weekly newsletter' angle)."""
from __future__ import annotations

import re

import markdown as _md
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BlogPost


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:180] or "post"


def unique_slug(session: Session, base: str, exclude_id: int | None = None) -> str:
    """Return `base`, or base-2, base-3… if taken by another post."""
    base = slugify(base)
    candidate, n = base, 1
    while True:
        row = session.scalar(select(BlogPost).where(BlogPost.slug == candidate))
        if row is None or row.id == exclude_id:
            return candidate
        n += 1
        candidate = f"{base}-{n}"


def render_markdown(md_text: str) -> str:
    return _md.markdown(md_text or "", extensions=["extra", "sane_lists", "nl2br"])


def build_stats_markdown(session: Session) -> str:
    """A ready-to-edit Markdown snapshot of the current live board, for a post."""
    from app.queries import Filters, analytics

    d = analytics(session, Filters())
    s = d["summary"]

    def top(dim, n=5):
        return ", ".join(f"{label} ({count})" for label, count in d[dim]["rows"][:n]) or "—"

    words = ", ".join(w for w, _ in d["wordcloud"][:12]) or "—"
    lines = [
        "## This week across Australian university jobs",
        "",
        f"- **{d['total']:,}** open roles on the board right now",
        f"- **{s['posted_7d']}** posted in the last 7 days; **{s['closing_7d']}** closing within 7 days",
        f"- **{s['salary_pct']}%** of roles publish a salary"
        + (f" (avg minimum ~${round(s['avg_salary_min']):,})" if s['avg_salary_min'] else ""),
        f"- Busiest states: {top('by_state')}",
        f"- Most active disciplines: {top('by_discipline')}",
        f"- Role mix: {top('by_role_family')}",
        "",
        f"**Trending in role titles:** {words}",
        "",
        "_Add your commentary and a quote or two from your university contacts here._",
        "",
    ]
    return "\n".join(lines)
