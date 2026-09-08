"""SQLAlchemy models.

Postgres-first: the ``listings.search_vector`` column is a generated ``tsvector``
with a GIN index, which is all the full-text search this dataset (a few thousand
rows) needs — no Elasticsearch.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class Base(DeclarativeBase):
    pass


# --- FTS expression reused by the generated column ---------------------------
_TSVECTOR_EXPR = (
    "to_tsvector('english', "
    "coalesce(title,'') || ' ' || coalesce(excerpt,'') || ' ' || "
    "coalesce(classification_raw,'') || ' ' || coalesce(university,'') || ' ' || "
    "coalesce(campus_location,''))"
)


class Listing(Base):
    """A single normalised job vacancy. One row per (university, source_job_id)."""

    __tablename__ = "listings"

    # Stable hash of (university_slug, source_job_id) — see ingest.normalise.make_id
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_job_id: Mapped[str] = mapped_column(String(255))

    university: Mapped[str] = mapped_column(String(255), index=True)
    university_slug: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(3), index=True)
    campus_location: Mapped[str | None] = mapped_column(String(255))

    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)

    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    closes_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    work_type: Mapped[str | None] = mapped_column(String(32), index=True)       # continuing/fixed-term/casual/contract
    time_fraction: Mapped[str | None] = mapped_column(String(16))               # full-time/part-time
    role_family: Mapped[str | None] = mapped_column(String(32), index=True)

    classification_raw: Mapped[str | None] = mapped_column(String(255))
    level_band: Mapped[str | None] = mapped_column(String(16), index=True)      # entry..leadership
    level_scale: Mapped[str | None] = mapped_column(String(16))                 # academic/professional
    pay_grade: Mapped[str | None] = mapped_column(String(32), index=True)       # "HEW 7"/"Academic Level B"/"Not specified"

    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_raw: Mapped[str | None] = mapped_column(String(255))

    discipline: Mapped[str | None] = mapped_column(String(64), index=True)
    remote_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    excerpt: Mapped[str | None] = mapped_column(Text)

    first_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open/closed/expired

    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed(_TSVECTOR_EXPR, persisted=True)
    )

    __table_args__ = (
        Index("ix_listings_search_vector", "search_vector", postgresql_using="gin"),
        Index("ix_listings_state_role", "state", "role_family"),
    )


class StagingListing(Base):
    """Raw normalised records for one crawl run, before the safe upsert.

    Adapters write here first. The runner validates the run's yield, then upserts
    into ``listings``. A failed or suspicious adapter never touches live data.
    """

    __tablename__ = "staging_listings"

    pk: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("crawl_runs.id"), index=True)
    university_slug: Mapped[str] = mapped_column(String(64), index=True)

    # Same payload shape as Listing (subset) stored as columns for easy inspection.
    id: Mapped[str] = mapped_column(String(64))
    source_job_id: Mapped[str] = mapped_column(String(255))
    university: Mapped[str] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(3))
    campus_location: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(Text)
    posted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    closes_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    work_type: Mapped[str | None] = mapped_column(String(32))
    time_fraction: Mapped[str | None] = mapped_column(String(16))
    role_family: Mapped[str | None] = mapped_column(String(32))
    classification_raw: Mapped[str | None] = mapped_column(String(255))
    level_band: Mapped[str | None] = mapped_column(String(16))
    level_scale: Mapped[str | None] = mapped_column(String(16))
    pay_grade: Mapped[str | None] = mapped_column(String(32))
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    salary_raw: Mapped[str | None] = mapped_column(String(255))
    discipline: Mapped[str | None] = mapped_column(String(64))
    remote_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    excerpt: Mapped[str | None] = mapped_column(Text)


class CrawlRun(Base):
    """One full refresh cycle across all adapters."""

    __tablename__ = "crawl_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running/complete/failed
    universities_ok: Mapped[int] = mapped_column(Integer, default=0)
    universities_failed: Mapped[int] = mapped_column(Integer, default=0)

    adapter_runs: Mapped[list["AdapterRun"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class AdapterRun(Base):
    """Per-university health record for one crawl run. Powers the admin page."""

    __tablename__ = "adapter_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("crawl_runs.id"), index=True)
    university_slug: Mapped[str] = mapped_column(String(64), index=True)
    adapter: Mapped[str] = mapped_column(String(32))
    # ok / failed / skipped_robots / suspect_low_yield / no_change
    status: Mapped[str] = mapped_column(String(24), index=True)
    records_returned: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[CrawlRun] = relationship(back_populates="adapter_runs")


class SavedSearch(Base):
    """A stored filter set the user can revisit or subscribe to via RSS."""

    __tablename__ = "saved_searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # opaque, for RSS URL
    name: Mapped[str] = mapped_column(String(255))
    query_string: Mapped[str] = mapped_column(Text)  # urlencoded filter params
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceResolution(Base):
    """Cached result of auto-discovering a university's real ATS endpoint.

    Lets the deployed crawler self-heal: it resolves each careers page to the
    correct adapter + params once, caches it, and reuses it until it goes stale or
    starts failing — so wrong config guesses stop mattering.
    """

    __tablename__ = "source_resolutions"

    university_slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    adapter: Mapped[str] = mapped_column(String(32))
    params_json: Mapped[str] = mapped_column(Text)          # JSON-encoded params dict
    matched_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="discovered")  # discovered/config
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FeaturedJob(Base):
    """Admin-curated 'featured' listing shown in a tile at the top of the board,
    with an optional recruiter interview video. `listing_id` is a plain reference
    (no FK) so listing churn during crawls never breaks curation; the display
    query left-joins and shows only featured whose listing is still open."""

    __tablename__ = "featured_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    listing_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=1)   # display order (1..3)
    video_url: Mapped[str | None] = mapped_column(Text)         # recruiter interview (optional)
    headline: Mapped[str | None] = mapped_column(Text)          # short 'why featured' blurb
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BlogPost(Base):
    """A blog / newsletter post (admin-authored). Body stored as Markdown source
    plus rendered HTML."""

    __tablename__ = "blog_posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    subtitle: Mapped[str | None] = mapped_column(Text)         # dek / excerpt
    body_md: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default="draft", index=True)  # draft/published
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    published_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class HttpCache(Base):
    """Conditional-request cache: ETag / Last-Modified per URL (see CRAWLING.md)."""

    __tablename__ = "http_cache"

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
