"""Replay a data migration on a scratch Postgres database.

pytest runs with --nomigrations, so a data migration that rewrites rows ships with
a replay test that migrates to the parent, writes rows, migrates to the target and
reads them back. Delete the replay test once a published release has applied the
migration.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.test import override_settings


@contextmanager
def scratch_database(django_db_blocker) -> Iterator[DatabaseWrapper]:
    """Swap an empty database in as `default`; build a `MigrationExecutor` on it."""
    settings = connections["default"].settings_dict
    name = f"migration_{uuid.uuid4().hex[:12]}"
    admin = psycopg2.connect(
        dbname="postgres",
        user=settings["USER"],
        password=settings["PASSWORD"],
        host=settings["HOST"],
        port=settings["PORT"],
    )
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE "{name}"')
    original = connections["default"]
    database = DatabaseWrapper({**settings, "NAME": name}, alias="default")
    try:
        with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
            connections["default"] = database
            try:
                yield database
            finally:
                database.close()
                connections["default"] = original
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        admin.close()
