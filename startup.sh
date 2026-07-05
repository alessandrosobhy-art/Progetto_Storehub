#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-2}"
GUNICORN_THREADS="${GUNICORN_THREADS:-8}"
GUNICORN_TIMEOUT="${GUNICORN_TIMEOUT:-180}"

# gthread: l'app è I/O-bound (attende Supabase), i thread evitano che
# poche richieste lente saturino i worker sync.
exec gunicorn \
  --bind "0.0.0.0:${PORT}" \
  --workers "${GUNICORN_WORKERS}" \
  --worker-class gthread \
  --threads "${GUNICORN_THREADS}" \
  --timeout "${GUNICORN_TIMEOUT}" \
  wsgi:app
