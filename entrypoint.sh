#!/usr/bin/env bash
# Entrypoint dispatch: `web` (API + UI) or `worker` (scheduled ingestion).
set -euo pipefail

wait_for_db() {
  echo "waiting for database…"
  for i in $(seq 1 30); do
    if python -c "from app.db import engine; engine.connect().close()" 2>/dev/null; then
      echo "database ready"; return 0
    fi
    sleep 1
  done
  echo "database did not become ready in time" >&2; exit 1
}

case "${1:-web}" in
  web)
    wait_for_db
    python -m app.ingest.runner --init-db
    # Seed only if the board is empty, so `docker compose up` shows data at once.
    python -c "
from app.db import SessionLocal
from app.models import Listing
from sqlalchemy import select, func
s = SessionLocal()
n = s.scalar(select(func.count()).select_from(Listing)) or 0
s.close()
import sys; sys.exit(0 if n > 0 else 7)
" || { echo 'seeding initial data…'; python -m app.seed; }
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    wait_for_db
    exec python -m app.scheduler
    ;;
  *)
    exec "$@"
    ;;
esac
