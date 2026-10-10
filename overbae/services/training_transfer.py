import json
import math
import tempfile
from pathlib import Path

import modal
from modal.exception import ConnectionError as ModalConnectionError
from modal.exception import InternalError, NotFoundError, ServiceError

from modal_shared.training_data import write_selection
from overbae.services import training_monitoring, training_release, training_submission

STAGES = {
    "building_selections": "Building row selections",
    "uploading_selections": "Uploading row selections",
    "waiting_for_materialization": "Row selections uploaded; awaiting provider progress",
    "verifying_prepared_data": "Verifying prepared data",
    "indexing_prepared_rows": "Indexing prepared rows",
    "selecting_prepared_rows": "Selecting prepared rows",
    "committing_training_data": "Saving training files",
    "training_data_ready": "Training files ready",
}


def stage_data(job, paths, run_id, preparation, volume, upload_fn, *, num_examples=None):
    details = {
        "run_id": run_id,
        "files_total": len(paths),
        "files_completed": 0,
        "acknowledged_bytes": 0,
    }

    def report(values):
        training_submission.record_pre_dispatch_stage(
            job, "transferring", STAGES[values["stage"]], diagnostics={**details, **values}
        )

    source_bytes = sum(Path(source).stat().st_size for source in paths.values())
    consumed = 0

    def building(values):
        report({**values, "completed": consumed + values["completed"], "total": source_bytes})

    selections = {}
    with tempfile.TemporaryDirectory(prefix="training-selection-") as directory:
        for name, source in paths.items():
            selections[name] = write_selection(
                source, Path(directory) / f"{name}.keys", progress=building
            )
            consumed += Path(source).stat().st_size
        if num_examples is not None and selections["data"]["rows"] != num_examples:
            raise ValueError("The training selection does not match the selected row count.")
        total_bytes = sum(spec["rows"] * 32 for spec in selections.values())
        for name in paths:
            report(
                {
                    "stage": "uploading_selections",
                    "completed": details["acknowledged_bytes"],
                    "total": total_bytes,
                    "unit": "bytes",
                    "measurement": "provider_acknowledged_files",
                }
            )
            # put_file only queues bytes; successful context exit acknowledges the file.
            with volume.batch_upload() as batch:
                batch.put_file(
                    Path(directory) / f"{name}.keys", f"/runs/{run_id}/selected-{name}.keys"
                )
            details["files_completed"] += 1
            details["acknowledged_bytes"] += selections[name]["rows"] * 32
        report(
            {
                "stage": "uploading_selections",
                "completed": total_bytes,
                "total": total_bytes,
                "unit": "bytes",
                "measurement": "provider_acknowledged_files",
            }
        )
    report({"stage": "waiting_for_materialization"})
    result = upload_fn.remote(
        run_id=run_id,
        preparation_id=str(preparation.id),
        artifact_sha256=preparation.report["artifact_sha256"],
        selections=selections,
        monitoring=(job.hyperparameters or {}).get("monitoring"),
    )
    if isinstance(result, dict) and result.get("monitoring_plan"):
        training_monitoring.save_plan(job, result["monitoring_plan"])


def observe(job, *, volume=None):
    progress = job.progress or {}
    details = progress.get("diagnostics") or {}
    if (
        job.status != "preparing"
        or progress.get("stage") != "transferring"
        or not details.get("run_id")
    ):
        return
    try:
        if volume is None:
            volume = modal.Volume.from_name(
                "overmind-sft", environment_name=training_release.for_job(job)["environment"]
            )
        content = bytearray()
        for chunk in volume.read_file(f"/runs/{details['run_id']}/transfer-progress.json"):
            content.extend(chunk)
            if len(content) > 16384:
                return
        raw = json.loads(content)
        if not isinstance(raw, dict) or raw.get("stage") not in STAGES or not raw.get("source_at"):
            return
        values = {
            key: raw[key]
            for key in ("source_at", "completed", "total")
            if type(raw.get(key)) in (int, float) and math.isfinite(raw[key]) and raw[key] >= 0
        }
        if "source_at" not in values:
            return
        values.update(
            stage=raw["stage"],
            unit=raw.get("unit") if raw.get("unit") in {"rows", "bytes"} else None,
        )
        retained = {
            key: details[key]
            for key in ("run_id", "files_total", "files_completed", "acknowledged_bytes")
            if key in details
        }
        training_submission.record_pre_dispatch_stage(
            job,
            "transferring",
            STAGES[raw["stage"]],
            diagnostics={**retained, **values},
            expected_stage="transferring",
        )
    except (
        OSError,
        ValueError,
        TypeError,
        ModalConnectionError,
        InternalError,
        ServiceError,
        NotFoundError,
    ):
        # Observation failure cannot fail or replay the training submission.
        return
