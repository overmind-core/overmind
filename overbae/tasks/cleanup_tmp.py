"""Periodic cleanup of /data/tmp to prevent unbounded EFS growth."""

import logging
import os
import shutil
import time

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

_MAX_AGE_SECONDS = 6 * 3600


@shared_task(name="overbae.tasks.cleanup_tmp.cleanup_data_tmp")
def cleanup_data_tmp() -> dict[str, int]:
    tmpdir = getattr(settings, "TMPDIR", "/data/tmp")
    if not os.path.isdir(tmpdir):
        return {"skipped": True}

    cutoff = time.time() - _MAX_AGE_SECONDS
    removed_files = 0
    removed_dirs = 0
    errors = 0

    for entry in os.scandir(tmpdir):
        # HOME == TMPDIR in our containers — dotfiles are config
        # (.trussrc, .aws, …), not scratch. Never sweep them.
        if entry.name.startswith("."):
            continue
        try:
            mtime = entry.stat(follow_symlinks=False).st_mtime
            if mtime > cutoff:
                continue
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
                removed_dirs += 1
            else:
                os.unlink(entry.path)
                removed_files += 1
        except OSError:
            errors += 1

    if removed_files or removed_dirs:
        logger.info(
            "cleanup_data_tmp: removed %d files, %d dirs (errors=%d)",
            removed_files,
            removed_dirs,
            errors,
        )
    return {"removed_files": removed_files, "removed_dirs": removed_dirs, "errors": errors}
