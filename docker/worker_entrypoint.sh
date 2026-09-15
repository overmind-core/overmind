#!/bin/sh
# Celery worker entry point.
# Shares all setup with the main entrypoint (trussrc, etc.) but skips the
# DB bootstrap step — workers don't run migrations.
RUN_DB_BOOTSTRAP=0 exec /usr/local/bin/docker-entrypoint.sh "$@"
