from __future__ import annotations

from pathlib import Path
from typing import Any


def media_root() -> Path:
    from django.conf import settings

    return Path(settings.MEDIA_ROOT)


def dataset_dir(dataset_id: Any) -> Path:
    return media_root() / "datasets" / str(dataset_id)


def cell_path(dataset_id: Any, cell_id: Any) -> Path:
    return dataset_dir(dataset_id) / "cells" / f"{cell_id}.parquet"


def source_path(dataset_id: Any, artifact_id: str) -> Path:
    return dataset_dir(dataset_id) / "sources" / artifact_id


def attachment_path(dataset_id: Any, cell_id: Any) -> Path:
    return dataset_dir(dataset_id) / "attachments" / f"{cell_id}.parquet"


def workspace_dir(dataset_id: Any) -> Path:
    return dataset_dir(dataset_id) / "workspace"


def library_cache(project_id: Any) -> Path:
    return media_root() / "libraries" / str(project_id)
