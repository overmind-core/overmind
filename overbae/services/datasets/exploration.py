import json
import logging
import sqlite3
import time
import uuid
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from overbae.core.errors import InputValidationError
from overbae.models import DataExploration, Dataset, Project
from overbae.services.datasets import land, paths, profile, rows, sampling, store, use
from overbae.services.datasets.examples import native_decision
from overbae.services.datasets.partition import preserve_lineage

logger = logging.getLogger(__name__)
PROFILE_VERSION = 2


@transaction.atomic
def request(project, *, source_cell, name, request_key, kind, sampling_request=None):
    if source_cell.dataset.project_id != project.pk or kind not in {"profile", "derive"}:
        raise InputValidationError(
            "Select a source version in this project and a supported operation"
        )
    config = {
        "profile_version": PROFILE_VERSION,
        "sampling": sampling.validate_request(**sampling_request) if sampling_request else None,
    }
    Project.objects.select_for_update().get(pk=project.pk)
    prior = DataExploration.objects.filter(project=project, request_key=request_key).first()
    if prior:
        if (
            prior.source_cell_id != source_cell.pk
            or prior.kind != kind
            or prior.config.get("sampling") != config["sampling"]
            or prior.name != name
        ):
            raise InputValidationError("This request key identifies a different exploration")
        if prior.state == "failed":
            prior.state, prior.error = "queued", ""
            prior.save()
        return prior
    Dataset.objects.select_for_update().get(pk=source_cell.dataset_id)
    source = source_cell.dataset.cells.get(pk=source_cell.pk)
    if not source.ran:
        raise InputValidationError("Select a readable source version")
    use.freeze(source)
    return DataExploration.objects.create(
        project=project,
        name=name,
        request_key=request_key,
        source_cell=source,
        source_fingerprint=source.fingerprint,
        kind=kind,
        config=config,
    )


def advance(identifier):
    with transaction.atomic():
        op = (
            DataExploration.objects.select_for_update()
            .select_related("source_cell__dataset", "project")
            .get(pk=identifier)
        )
        if op.state == "completed" or (
            op.state == "running" and op.updated_at > timezone.now() - timedelta(seconds=1260)
        ):
            return
        op.state, op.error = "running", ""
        op.save()
    started = time.perf_counter()
    try:
        rows.verify(op.source_cell)
        if op.source_cell.fingerprint != op.source_fingerprint:
            raise InputValidationError("The selected source fingerprint changed")
        path = rows.frame_path(op.source_cell)
        report = {"source": use.describe(op.source_cell)}
        if op.kind == "profile":
            cached = (
                DataExploration.objects.filter(
                    project=op.project,
                    kind="profile",
                    state="completed",
                    source_fingerprint=op.source_fingerprint,
                    config=op.config,
                )
                .exclude(pk=op.pk)
                .first()
            )
            if cached:
                report = {
                    **cached.report,
                    "source": use.describe(op.source_cell),
                    "reused_from": str(cached.pk),
                }
            else:
                report["profile"] = profile.profile_records(store.iter_rows(path))
                if op.config["sampling"]:
                    counts = sampling.census(
                        lambda: store.iter_frames(path), native_decision, op.config["sampling"]
                    )
                    report["sampling"] = sampling.feasibility(counts, op.config["sampling"])
                    quotas = sampling.allocation(counts, op.config["sampling"])
                    artifact = paths.media_root() / "explorations" / str(op.pk) / "strata.sqlite"
                    artifact.parent.mkdir(parents=True, exist_ok=True)
                    with sqlite3.connect(artifact) as db:
                        db.execute(
                            "CREATE TABLE IF NOT EXISTS strata (stratum TEXT PRIMARY KEY, count INTEGER NOT NULL, allocation INTEGER)"
                        )
                        db.execute("DELETE FROM strata")
                        db.executemany(
                            "INSERT INTO strata VALUES (?, ?, ?)",
                            (
                                (key, count, quotas.get(key))
                                for key, count in sorted(counts.items())
                            ),
                        )
                    report["sampling"]["artifact"] = str(op.pk)
                    report["sampling"]["coverage_tradeoff"] = (
                        "Every stratum retained"
                        if quotas
                        else "No allocation chosen; change the requested size or explicitly revise strata"
                    )
                report["measurement"] = {
                    "population": "whole_source",
                    "elapsed_seconds": time.perf_counter() - started,
                    "resource_envelope": {
                        "soft_timeout_seconds": 1200,
                        "hard_timeout_seconds": 1260,
                        "max_strata": 100000,
                        "max_profile_columns": 100,
                        "max_family_examples": 8,
                    },
                }

        else:

            def records():
                for index, record in enumerate(store.iter_rows(path)):
                    lineage = preserve_lineage(record)
                    lineage["derived_from"] = {
                        "cell": str(op.source_cell_id),
                        "fingerprint": op.source_fingerprint,
                        "row": index,
                    }
                    yield {**record, "_overmind_provenance": lineage}

            with transaction.atomic():
                source = op.source_cell.dataset
                derived, _ = Dataset.objects.get_or_create(
                    pk=uuid.uuid5(op.pk, "output"),
                    defaults={
                        "project": op.project,
                        "name": op.name,
                        "brief": source.brief,
                        "intent": source.intent,
                        "capability_id": source.capability_id,
                    },
                )
                if not derived.active_cell:
                    land.commit(
                        derived,
                        land.Landing(records(), spec={"derived_from": report["source"]}),
                        infer_capability=False,
                    )
                rows.verify(derived.active_cell)
                op.output_dataset = derived
                report["output"] = use.describe(derived.active_cell)
        DataExploration.objects.filter(pk=op.pk).update(
            state="completed",
            report=report,
            output_dataset=op.output_dataset,
            error="",
            updated_at=timezone.now(),
        )
    except Exception:
        logger.exception("Data exploration failed: %s", identifier)
        DataExploration.objects.filter(pk=op.pk).update(
            state="failed",
            error="Source exploration failed. Verify the selected version and sampling fields before retrying.",
            updated_at=timezone.now(),
        )


def describe(op):
    return {
        "id": str(op.pk),
        "name": op.name,
        "state": op.state,
        "kind": op.kind,
        "source_cell": str(op.source_cell_id),
        "source_fingerprint": op.source_fingerprint,
        "config": op.config,
        "report": op.report,
        "output_dataset": str(op.output_dataset_id) if op.output_dataset_id else None,
        "error": op.error,
        "strata_resource": f"overmind://jobs/data_exploration/{op.pk}/strata"
        if op.report.get("sampling")
        else None,
    }


def strata(op, *, limit=100, offset=0):
    if op.state != "completed" or not op.report.get("sampling"):
        raise InputValidationError("Complete a sampling exploration before reading its strata")
    if not 1 <= limit <= 100 or offset < 0:
        raise InputValidationError("Use a page size from 1 to 100 and a nonnegative offset")
    identifier = uuid.UUID(op.report["sampling"]["artifact"])
    artifact = paths.media_root() / "explorations" / str(identifier) / "strata.sqlite"
    with sqlite3.connect(f"file:{artifact}?mode=ro", uri=True) as db:
        values = db.execute(
            "SELECT stratum, count, allocation FROM strata ORDER BY stratum LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return {
        "total": op.report["sampling"]["strata"],
        "offset": offset,
        "strata": [
            {"values": json.loads(key), "count": count, "allocation": quota}
            for key, count, quota in values
        ],
    }
