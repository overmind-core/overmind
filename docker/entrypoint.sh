#!/bin/sh
# Bootstrap DB before any Django process (migrate + idempotent data seeds).
# Disable with RUN_DB_BOOTSTRAP=0 (e.g. production where migrate runs in CI).
set -e

# Write ~/.trussrc so truss_train.push() can authenticate with Baseten.
if [ -n "${BASETEN_API_KEY}" ]; then
  _HOME="${HOME:-/root}"
  mkdir -p "$_HOME"
  cat > "$_HOME/.trussrc" << TRUSSRC
[baseten]
remote_provider = baseten
auth_type = api_key
api_key = ${BASETEN_API_KEY}
remote_url = https://app.baseten.co
TRUSSRC
fi

if [ "${RUN_DB_BOOTSTRAP:-1}" = "1" ]; then
  echo "==> migrate"
  python manage.py migrate --noinput
  echo "==> seed_evaluators"
  python manage.py seed_evaluators
fi

# Instrument with New Relic only when the license key is injected (production:
# NEW_RELIC_LICENSE_KEY comes from Secrets Manager, NEW_RELIC_APP_NAME from the
# ECS task definition). The agent reads both from the environment — no config
# file needed. Wrapping here covers web and celery regardless of the container
# command; local/dev runs without the key are untouched.
if [ -n "${NEW_RELIC_LICENSE_KEY}" ]; then
  set -- newrelic-admin run-program "$@"
fi

exec "$@"
