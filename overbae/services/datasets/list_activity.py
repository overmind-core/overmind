from overbae.models import Activity, AssistantThread, DatasetPipelineRun, DatasetTransfer


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
            "label": "Previewing" if run.mode == "preview" else "Transforming",
            "status": run.state,
            "actor_kind": actors.get(str(run.pk)),
            "completed": None,
            "total": None,
            "unit": "",
        }
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
