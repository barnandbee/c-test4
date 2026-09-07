"""Adapter base class + the polite HTTP client.

The client enforces every rule in CRAWLING.md: robots.txt, an identifying
User-Agent, per-domain rate limiting, exponential backoff, and conditional
requests (ETag / If-Modified-Since). Adapters never fetch directly — they go
through ``PoliteClient`` so the conduct rules can't be bypassed by accident.

Adapters split into two halves so they're testable without a network:
    endpoints(university) -> [url, ...]        which URLs to fetch
    parse(content, university) -> [RawJob]     pure parsing, tested against fixtures
"""
from __future__ import annotations

import abc
import datetime as dt
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass

import httpx

from app.config import get_settings
from app.normalise.core import RawJob


@dataclass
class FetchResult:
    url: str
    status_code: int
    content: bytes
    not_modified: bool = False
    final_url: str = ""   # URL after following redirects (for platform discovery)


class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids the path — the adapter skips, not works around."""


class PoliteClient:
    """A single-threaded, rate-limited, robots-respecting HTTP client.

    Pass a ``cache`` mapping (url -> {'etag', 'last_modified'}) to enable
    conditional requests; the runner backs this with the ``http_cache`` table.
    """

    def __init__(self, cache: dict | None = None):
        self._settings = get_settings()
        self._last_request_at: dict[str, float] = {}     # domain -> monotonic ts
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._cache = cache if cache is not None else {}
        self._client = httpx.Client(
            headers={"User-Agent": self._settings.user_agent},
            timeout=self._settings.request_timeout_seconds,
            follow_redirects=True,
        )

    # -- lifecycle -----------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- politeness ----------------------------------------------------------
    @staticmethod
    def _domain(url: str) -> str:
        return urllib.parse.urlsplit(url).netloc

    def _robots_ok(self, url: str) -> bool:
        if not self._settings.respect_robots:
            return True
        domain = self._domain(url)
        parser = self._robots.get(domain)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            robots_url = urllib.parse.urlunsplit(
                (urllib.parse.urlsplit(url).scheme, domain, "/robots.txt", "", "")
            )
            try:
                resp = self._client.get(robots_url)
                if resp.status_code == 200:
                    parser.parse(resp.text.splitlines())
                else:
                    parser.parse([])  # no robots.txt -> allow
            except httpx.HTTPError:
                parser.parse([])
            self._robots[domain] = parser
        return parser.can_fetch(self._settings.user_agent, url)

    def _throttle(self, domain: str) -> None:
        gap = self._settings.request_delay_seconds
        last = self._last_request_at.get(domain)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < gap:
                time.sleep(gap - elapsed)
        self._last_request_at[domain] = time.monotonic()

    # -- fetch ---------------------------------------------------------------
    def fetch(self, url: str, method: str = "GET", **kwargs) -> FetchResult:
        """Fetch a URL respecting robots, rate limit, backoff and caching."""
        if not self._robots_ok(url):
            raise RobotsDisallowed(url)

        domain = self._domain(url)
        headers = dict(kwargs.pop("headers", {}))
        cached = self._cache.get(url)
        if cached:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(self._settings.max_retries):
            self._throttle(domain)
            try:
                resp = self._client.request(method, url, headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                last_exc = exc
                time.sleep(backoff)
                backoff *= 2
                continue

            if resp.status_code == 304:
                return FetchResult(url, 304, b"", not_modified=True)
            if resp.status_code >= 500:
                last_exc = httpx.HTTPStatusError(
                    f"{resp.status_code}", request=resp.request, response=resp
                )
                time.sleep(backoff)
                backoff *= 2
                continue
            resp.raise_for_status()

            # Remember validators for next time.
            self._cache[url] = {
                "etag": resp.headers.get("ETag"),
                "last_modified": resp.headers.get("Last-Modified"),
            }
            return FetchResult(url, resp.status_code, resp.content, final_url=str(resp.url))

        raise last_exc or httpx.HTTPError(f"failed to fetch {url}")


# -------------------------------------------------------------------------
# Adapter contract
# -------------------------------------------------------------------------
class Adapter(abc.ABC):
    """Base class for a platform-family adapter."""

    name: str = "base"

    @abc.abstractmethod
    def endpoints(self, university: dict) -> list[dict]:
        """Return a list of {'url', 'method', 'kwargs'} fetch specs."""

    @abc.abstractmethod
    def parse(self, content: bytes, university: dict, source_url: str) -> list[RawJob]:
        """Parse one response body into RawJobs. Pure — no I/O. Tested via fixtures."""

    def fetch(self, client: PoliteClient, university: dict) -> list[RawJob]:
        """Default orchestration: fetch every endpoint, parse, concatenate.

        Returns as soon as one endpoint yields jobs (endpoints are ordered by
        preference: feed > JSON > HTML). A 304 Not-Modified is treated as
        'no change', signalled by returning the sentinel below.
        """
        jobs: list[RawJob] = []
        for spec in self.endpoints(university):
            result = client.fetch(spec["url"], spec.get("method", "GET"), **spec.get("kwargs", {}))
            if result.not_modified:
                raise NotModified(spec["url"])
            parsed = self.parse(result.content, university, spec["url"])
            if parsed:
                jobs.extend(parsed)
                break  # first successful endpoint wins
        return jobs


class NotModified(Exception):
    """The endpoint returned 304 — nothing to do, keep existing listings."""


# -------------------------------------------------------------------------
# Shared date parsing helpers (ATS feeds use a zoo of formats)
# -------------------------------------------------------------------------
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%d %b %Y",
    "%d %B %Y",
    "%a, %d %b %Y %H:%M:%S %z",   # RSS pubDate
    "%A, %d %B %Y",
]


def parse_date(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    value = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed
        except ValueError:
            continue
    return None


def epoch_ms_to_dt(value) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(value) / 1000, tz=dt.timezone.utc)
    except (TypeError, ValueError):
        return None
