#!/bin/sh
# Start the API, then optionally seed demo data once.
set -e

uvicorn app.main:app --host 0.0.0.0 --port 8000 &
APP_PID=$!

if [ "${XRECON_SEED_SAMPLES:-0}" = "1" ]; then
  python /app/scripts/seed_demo.py || echo "[entrypoint] seeding failed (continuing)"
fi

wait $APP_PID
