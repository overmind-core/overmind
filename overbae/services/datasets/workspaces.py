from collections import defaultdict

from overbae.models import DataExploration, DataPartitionMember


def workspace_ids(datasets):
    projects = {dataset.project_id for dataset in datasets}
    parents = defaultdict(set)
    partitions = DataPartitionMember.objects.filter(
        plan__project_id__in=projects, plan__state="completed"
    ).values_list("cell__dataset_id", "plan__source_cell__dataset_id")
    explorations = DataExploration.objects.filter(
        project_id__in=projects, state="completed", output_dataset__isnull=False
    ).values_list("output_dataset_id", "source_cell__dataset_id")
    for child, parent in [*partitions, *explorations]:
        if child != parent:
            parents[child].add(parent)
    result = {}
    for dataset in datasets:
        current = dataset.pk
        seen = {current}
        while len(parents[current]) == 1:
            parent = next(iter(parents[current]))
            if parent in seen:
                current = dataset.pk
                break
            seen.add(parent)
            current = parent
        result[str(dataset.pk)] = current
    return result
