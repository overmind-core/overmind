from overbae.models import Activity, AssistantThread, DatasetPipelineRun, DatasetTransfer

LANDING_STAGES = {"writing": "Writing", "measuring": "Measuring"}
RUN_STAGES = {
    "writing": "Writing",
    "checking_rows": "Checking rows",
    "measuring": "Measuring",
    "publishing": "Publishing",
}


def run_progress(run) -> dict:
    """A run's stage as the Console names it, with its counter: the sandbox's completed
    batches while a batched step executes, then the rows written and measured so far."""
    result = run.result or {}
    stage = str(result.get("stage") or "")
    steps = result.get("steps") or []
    label = "Previewing" if run.mode == "preview" else "Transforming"
    progress = {"label": label, "completed": None, "total": None, "unit": ""}
    if stage.startswith("step_"):
        index, _, phase = stage.removeprefix("step_").partition("_")
        receipt = steps[int(index) - 1] if index.isdigit() and int(index) <= len(steps) else {}
        if phase in RUN_STAGES:
            progress["label"] = RUN_STAGES[phase]
        elif (receipt.get("runtime") or {}).get("stage") == "waiting_for_sandbox":
            progress["label"] = "Waiting for sandbox"
        batches = receipt.get("batches") or {}
        if phase in ("writing", "measuring") and result.get("rows_done") is not None:
            # The output's size is known only once it is written.
            total = receipt.get("output_rows") if phase == "measuring" else None
            progress |= {"completed": result["rows_done"], "total": total, "unit": "rows"}
        if phase == "executing" and batches.get("total"):
            progress |= {
                "completed": batches["completed"],
                "total": batches["total"],
                "unit": "batches",
            }
    elif stage in RUN_STAGES:
        progress["label"] = RUN_STAGES[stage]
    return progress


def current_work(datasets):
    ids = [str(dataset.pk) for dataset in datasets]
    work = {}
    runs = list(
        DatasetPipelineRun.objects.filter(
            dataset_id__in=ids, state__in=["queued", "running"]
        ).order_by("created_at")
    )
    actors = dict(
        Activity.objects.filter(
            project_id__in={dataset.project_id for dataset in datasets},
            task_kind="dataset_pipeline",
            task_id__in=[str(run.pk) for run in runs],
        ).values_list("task_id", "actor_kind")
    )
    for run in runs:
        work[str(run.dataset_id)] = {
            **run_progress(run),
            "status": run.state,
            "actor_kind": actors.get(str(run.pk)),
        }
    for dataset in datasets:
        landing = (dataset.source_spec or {}).get("landing_progress") or {}
        if dataset.state == "landing" and landing.get("stage") in LANDING_STAGES:
            work.setdefault(
                str(dataset.pk),
                {
                    "label": LANDING_STAGES[landing["stage"]],
                    "status": "running",
                    "actor_kind": None,
                    "completed": landing.get("rows_done"),
                    "total": landing.get("rows_total"),
                    "unit": "rows" if landing.get("rows_done") is not None else "",
                },
            )
    for transfer in DatasetTransfer.objects.filter(
        specification__dataset__in=ids, state="uploading"
    ).order_by("created_at"):
        members = transfer.specification.get("files") or [transfer.specification]
        work[transfer.specification["dataset"]] = {
            "label": "Uploading",
            "actor_kind": None,
            "status": "running",
            "completed": transfer.received,
            "total": sum(member["size"] for member in members),
            "unit": "bytes",
        }
    # A conversation whose turn runs is preparing its dataset, even between jobs; a turn
    # lost with its worker is not.
    for dataset_id in (
        AssistantThread.objects.moving()
        .filter(dataset_id__in=ids)
        .values_list("dataset_id", flat=True)
    ):
        work.setdefault(
            str(dataset_id),
            {
                "label": "Preparing",
                "status": "running",
                "actor_kind": "overmind",
                "completed": None,
                "total": None,
                "unit": "",
            },
        )
    return work
