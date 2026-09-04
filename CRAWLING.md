# Crawling conduct — non-negotiable rules

These rules are enforced in code (`app/ingest/base.py` `PoliteClient`) and must
stay enforced. They exist because we are guests on 42 universities' servers.

## The rules

1. **Respect `robots.txt`.** Every fetch checks the domain's `robots.txt` first
   (`PoliteClient._robots_ok`). If a path is disallowed, the adapter records
   `skipped_robots` and moves on. **We never work around a disallow.** If a site
   you need is blocked, that's a documented gap, not a problem to engineer past.

2. **Identify ourselves.** Every request sends a real User-Agent naming the crawler
   and a contact email, e.g.
   `AusUniJobsBoard/0.1.0 (+jobsboard@example.org)`.
   Set a genuine, monitored address in `.env` (`CRAWLER_CONTACT_EMAIL`) before
   crawling live so site admins can reach a human.

3. **Rate limit hard.** At most **one request every `REQUEST_DELAY_SECONDS`
   (default 4s) per domain** (`PoliteClient._throttle`). On errors we back off
   exponentially (2s → 4s → 8s → 16s, `MAX_RETRIES` times). The full refresh runs
   at most **`REFRESH_TIMES_PER_DAY` (default 3)** times a day. Job postings change
   slowly; there is never a reason to hammer these servers.

4. **Cache and use conditional requests.** We store each URL's `ETag` /
   `Last-Modified` (`http_cache` table) and send `If-None-Match` /
   `If-Modified-Since` on the next crawl. A `304 Not Modified` short-circuits the
   adapter (`NotModified`) — existing listings are kept untouched, nothing is
   re-parsed.

5. **Store only what we need to index and link.** Title, employer, location, dates,
   classification, salary, and a **short excerpt** (≤ ~300 chars, truncated in
   `normalise.core._clean_excerpt`). We do **not** store or republish full job
   descriptions. Every listing links out to the original posting on the
   university's own site, and that link is the primary call to action in the UI.

6. **Primary sources only.** We crawl universities' own careers systems. We do
   **not** scrape SEEK, Indeed, LinkedIn, Glassdoor or any aggregator — their
   terms prohibit it and they add nothing here.

7. **A bad crawl must not destroy good data.** If an adapter returns zero (or far
   fewer) results than last time, that's treated as a probable failure
   (`suspect_low_yield`), not an empty result set. Existing listings are **kept,
   not closed** (`app/ingest/reconcile.py`). See `MIN_YIELD_RATIO`.

## Where each rule lives

| Rule | Enforced in |
|---|---|
| robots.txt | `PoliteClient._robots_ok` |
| User-Agent + contact | `Settings.user_agent`, sent on every request |
| Rate limit + backoff | `PoliteClient._throttle`, `PoliteClient.fetch` |
| Conditional requests | `PoliteClient.fetch` + `http_cache` table |
| Excerpt-only storage | `normalise.core._clean_excerpt` |
| No aggregators | config only lists primary sources; adding one is a review gate |
| Bad-crawl protection | `reconcile.plan_reconciliation` |

## If you change ingestion

Any change that could increase request volume (shorter delays, more frequent
refreshes, per-job detail fetches) must be justified against these rules. Prefer
feeds and JSON APIs over HTML scraping (feed > JSON > HTML) precisely because they
are cheaper on the source server.
