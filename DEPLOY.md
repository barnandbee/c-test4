# Deploying to Render

The repo includes a [`render.yaml`](./render.yaml) Blueprint that stands up the
whole board — a managed Postgres database and a Dockerised web service with an
in-process ingestion scheduler — on Render's **free tier**, with no separate
paid worker.

## One-time deploy

1. Push this repo to GitHub (already done for the feature branch). Merge to your
   default branch, or point Render at the branch you want to deploy.
2. In Render: **New → Blueprint**, connect the repo, and select it. Render reads
   `render.yaml` and shows a plan: one **Postgres** + one **web service**.
3. It will ask for the one secret marked `sync: false`:
   - **`CRAWLER_CONTACT_EMAIL`** — set a real, monitored address. It goes in the
     crawler's User-Agent so site admins can reach a human (see `CRAWLING.md`).
4. Click **Apply**. First build takes a few minutes.

On first boot the web service creates the schema and loads seed data, so the
board is populated immediately — before any crawl runs. Your public URL is shown
in the Render dashboard (e.g. `https://jobs-board.onrender.com`).

- Board: `/`
- Source health: `/admin`
- API / RSS: `/api/listings`, `/rss?…`

## How ingestion runs

`render.yaml` sets `RUN_SCHEDULER_IN_WEB=true`, so the scheduler runs *inside* the
web process (a background thread) and refreshes `REFRESH_TIMES_PER_DAY` times a
day. This avoids needing a separate worker, which isn't on the free tier.

Render's free web service **spins down after ~15 minutes of inactivity**, which
also pauses the in-process scheduler; the next visit wakes it (a slow first load).
That's fine for a demo. For always-on ingestion, upgrade the web service to a paid
instance, or split ingestion into a dedicated service (below).

### Live crawling needs verified endpoints

Render services have outbound internet, so live crawling *can* work here (unlike
the build sandbox). But the ATS endpoints in `config/universities.yaml` were
researched, not live-verified. From the Render **Shell** tab on the web service:

```bash
python -m app.ingest.runner --verify          # probe every endpoint, print status
python -m app.ingest.runner --only uwa         # crawl one to sanity-check
```

Fix any endpoint in `config/universities.yaml` (a one-line edit) and redeploy.
Until then the board runs happily on seed data.

## Optional: a dedicated worker (paid tiers)

If you'd rather separate ingestion from the web process, add this to the
`services:` list in `render.yaml` and set `RUN_SCHEDULER_IN_WEB=false` on the web
service:

```yaml
  - type: worker            # requires a paid instance type
    name: jobs-worker
    runtime: docker
    plan: starter
    dockerfilePath: ./Dockerfile
    dockerCommand: worker
    envVars:
      - key: DATABASE_URL
        fromDatabase: { name: jobs-db, property: connectionString }
```

Or use a **Render Cron Job** running `python -m app.ingest.runner` on a schedule
(e.g. `0 */8 * * *`) — a good fit for the few-times-a-day cadence.

## Notes / gotchas

- **Free Postgres expires.** Render deletes free databases after their trial
  window. For anything beyond a demo, use a paid database plan.
- **`DATABASE_URL` format.** Render provides `postgres://…`; the app rewrites it to
  `postgresql+psycopg2://…` automatically (`Settings.sqlalchemy_url`), so no manual
  fixup is needed.
- **`$PORT`.** The container binds to Render's injected `$PORT` automatically.
- **Blueprint key names.** This uses the current spec (`runtime: docker`). If your
  Render account is on the older spec, change `runtime:` to `env:`.
