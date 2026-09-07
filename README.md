# Australian University Jobs Board

A self-hosted, continuously-updating jobs board that aggregates open vacancies
from all **42 Australian universities** into one searchable, filterable interface.

Ask it *"show me all continuing academic roles at Go8 universities in Victoria
posted in the last 7 days"* and get an answer in a couple of clicks — with every
listing linking out to the original posting on the university's own site.

> **Architecture in one line:** one adapter per applicant-tracking-system *family*
> (PageUp, Workday, NGA.NET, …) plus a per-university **config row** that says
> which adapter to use and with what parameters. Adding a university is a config
> edit, not new code.

---

## Quick start

```bash
cp .env.example .env          # set CRAWLER_CONTACT_EMAIL to a real address
docker compose up --build
```

Then open **http://localhost:8000**. Seed data loads automatically on first boot,
so the UI is populated before the first crawl finishes. The scheduled worker runs
a full refresh shortly after start and then a few times a day.

- Board: <http://localhost:8000/>
- Source health / admin: <http://localhost:8000/admin>
- JSON API: <http://localhost:8000/api/listings> · docs at `/api/docs`
- RSS for any filter set: `/rss?<same query params as the board>`

### Deploying

A [`render.yaml`](./render.yaml) Blueprint deploys the board (managed Postgres +
Dockerised web service with in-process scheduler) on Render's free tier. See
[`DEPLOY.md`](./DEPLOY.md).

### Running locally without Docker

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+psycopg2://jobs:jobs@localhost:5432/jobs
python -m app.ingest.runner --init-db
python -m app.seed                       # optional seed data
uvicorn app.main:app --reload            # UI on :8000
python -m app.scheduler                  # ingestion worker (separate process)
```

---

## How it works

```
config/universities.yaml   ─┐
                            │   runner picks each uni's adapter
app/ingest/<family>.py   ───┼──► RawJob  ──► normalise ──► staging table
 (pageup, workday, …)       │                                  │
                            │              safe reconcile (yield-guarded upsert)
                            └──────────────────────────► listings (Postgres + tsvector)
                                                              │
                                              FastAPI + server-rendered HTMX-style UI
```

- **Auto-discovery** (`app/ingest/discover.py`): before crawling, the runner fetches
  each university's careers page and detects the real ATS endpoint from it (Workday
  tenant/dc/site, PageUp client/path, SmartRecruiters company, NGA.NET host, Oracle
  site). The result is cached (`source_resolutions`) and reused for a week. This
  means the hand-configured endpoints in `universities.yaml` are just a *fallback* —
  wrong guesses self-correct on a host with network egress. Run it explicitly with
  `python -m app.ingest.runner --discover`.
- **Adapters** split into `endpoints()` (which URLs to fetch) and `parse()` (pure,
  fixture-tested). PageUp walks *all* listing pages (not just page one). Fetching
  goes through `PoliteClient`, which enforces every rule in [`CRAWLING.md`](./CRAWLING.md):
  robots.txt, identifying User-Agent, per-domain rate limiting, exponential backoff,
  and conditional requests.
- **Normalisation** is table-driven (`config/level_bands.yaml`,
  `role_families.yaml`, `disciplines.yaml`) — reviewable YAML, not scattered regex.
- **Reconciliation** (`app/ingest/reconcile.py`) is yield-guarded: a crawl that
  returns far fewer records than last time is `suspect_low_yield` and **keeps**
  existing listings rather than closing them.
- **Search** is Postgres `tsvector` full-text plus plain SQL facets — no
  Elasticsearch; the dataset is only a few thousand rows.

See [`DISCOVERY.md`](./DISCOVERY.md) for the platform-per-university coverage table
and honest confidence levels.

---

## Adding a university

Add one record to `config/universities.yaml`. No code needed if it's on a
supported platform (PageUp, Workday, NGA.NET, SmartRecruiters, or a bespoke HTML
page via `html_generic`).

**PageUp** (the common case):
```yaml
  - slug: example
    name: Example University
    state: VIC
    groups: [iru]                 # go8 / atn / iru / run — drives UI presets
    adapter: pageup
    careers_url: https://www.example.edu.au/careers
    params:
      listing_url: https://jobs.example.edu.au/cw/en/listing/
      feed_url: https://jobs.example.edu.au/cw/en/listing/?rss=1   # optional, preferred
    confidence: medium
```

**Workday:**
```yaml
    adapter: workday
    params: { tenant: example, dc: wd3, site: EXAMPLE_External_Careers }
    # endpoint POST https://example.wd3.myworkdayjobs.com/wday/cxs/example/EXAMPLE_External_Careers/jobs
```

**Bespoke HTML page** (`html_generic`) — everything is CSS selectors, so tuning is
a config edit:
```yaml
    adapter: html_generic
    params:
      listing_url: https://www.example.edu.au/employment
      row_selector: "article, .vacancy"
      title_selector: "h2 a, h3 a"
      link_selector: "a"
      location_selector: ".location"
      date_selector: "time"
```

Then verify and load:
```bash
python -m app.ingest.runner --verify --only example   # probe the endpoint (needs egress)
python -m app.ingest.runner --only example            # crawl just this one
```

Adding a **new platform family** = add one small `Adapter` subclass in
`app/ingest/`, register it in `app/ingest/registry.py`, and drop a fixture +
test in `tests/`. `SmartRecruitersAdapter` is a minimal worked example.

---

## Fixing a broken adapter

Sites change; adapters break. The system is built to make that a quick fix:

1. **Spot it.** The [`/admin`](http://localhost:8000/admin) page shows per-university
   status, records returned, last run, and stale flags. A red `failed` or amber
   `suspect_low_yield` tells you where to look. Existing listings are safe — a bad
   crawl never blanks them.
2. **Reproduce without re-crawling.** Every adapter has a saved fixture under
   `tests/fixtures/`. Run `pytest tests/test_adapters.py -k <family>` to see what
   the parser does. If the site's markup changed, **save the new response as a
   fixture** and update the test to the expected output — the test now describes
   the breakage.
3. **Fix the parse.** For `html_generic`, adjust the CSS selectors in
   `universities.yaml` — no code. For a platform adapter, fix the selector/JSON
   path in `app/ingest/<family>.py` until the fixture test passes.
4. **Verify against the live endpoint** (needs network egress to the site):
   ```bash
   python -m app.ingest.runner --verify --only <slug>
   python -m app.ingest.runner --only <slug>
   ```
5. Confirm the count on `/admin` recovers.

Logs (`app.scheduler` / the runner) record status, HTTP codes and the reconcile
reason per university, so you can debug a broken selector from the logs alone.

---

## Tests

```bash
pip install -r requirements.txt
pytest -q          # adapters (fixture-based), normalisation, reconciliation
```

Adapter tests run against saved fixtures, so they pass with no network and tell
you exactly what broke when a site changes.

---

## Project layout

```
config/          universities.yaml + normalisation tables (the "config not code" core)
app/
  ingest/        PoliteClient, adapters (one per platform family), runner, reconcile
  normalise/     level bands, role families, disciplines, salary parsing
  queries.py     search + facets (tsvector FTS)
  web/           FastAPI routes, Jinja templates, static assets
  models.py      SQLAlchemy schema (listings, staging, crawl/adapter health, saved searches)
  seed.py        illustrative seed data
  scheduler.py   APScheduler worker
tests/           fixtures + tests
CRAWLING.md      crawl conduct rules (enforced in code)
DISCOVERY.md     platform coverage table + confidence
```

## Status vs. the brief's definition of done

- ✅ 42 universities mapped to adapters; **33 at high/medium confidence** should
  ingest with zero or one-line config edits. Remaining low-confidence sites and 3
  bespoke tiny institutions are **documented gaps** in `DISCOVERY.md`.
- ✅ Full refresh runs unattended (scheduler), within the crawl rules, and never
  blanks listings on a bad crawl.
- ✅ Filter to any combination (e.g. VIC + academic + Level B/C + last 7 days) with
  accurate results that link out to the source.
- ⚠️ **Live endpoints were researched, not verified** — the build environment
  blocks outbound access to `*.edu.au`/ATS domains. Run
  `python -m app.ingest.runner --verify` from an environment with open egress to
  confirm each endpoint. This is the one step that must happen on real deployment.
