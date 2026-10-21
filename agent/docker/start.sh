#!/bin/sh
# Container start (Railway): harness readiness, then migrations, then uvicorn.
# Secrets come from the service's env vars at run time; nothing here is baked into the image.
set -eu

# `qloo setup --status` exits 1 while the optional chat model is unconfigured, so readiness
# is the `Qloo data: ready` line, not the exit code (decision 001). Offline; spends no quota.
status=$(qloo setup --status 2>&1 || true)
if ! printf '%s\n' "$status" | grep -q 'Qloo data: ready'; then
    printf '%s\n' "$status" >&2
    echo "start: Qloo harness not ready; set QLOO_API_KEY, QLOO_BASE_URL and QLOO_TRUSTED_BASE_URL." >&2
    exit 1
fi
echo "start: $(printf '%s\n' "$status" | grep 'Qloo data:' | sed 's/^ *//')"

if [ -z "${DATABASE_URL:-}" ]; then
    echo "start: DATABASE_URL is not set." >&2
    exit 1
fi
alembic upgrade head

exec uvicorn draw.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
    --proxy-headers --forwarded-allow-ips '*'
