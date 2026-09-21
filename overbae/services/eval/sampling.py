from __future__ import annotations

import heapq
import math
import random
from collections import defaultdict

from overbae.services.datasets import rows
from overbae.services.datasets.examples import input_objects


def select_rows(cell, *, limit: int | None, fraction: float = 1.0):
    if not limit and fraction == 1.0:
        return list(rows.iter_rows(cell))
    groups = defaultdict(list)
    configured = (cell.dataset.source_spec or {}).get("split", {}).get("stratify_by")
    fields = (
        [configured]
        if configured
        else ["worker_mode", "mode", "task_type", "behaviour_key", "label"]
    )
    for row in rows.iter_rows(cell):
        values = input_objects({**row.extra, "input": row.input})
        label = next(
            (
                (field, str(value[field]))
                for field in fields
                for value in values
                if isinstance(value.get(field), (str, int, bool)) and value[field] != ""
            ),
            ("", ""),
        )
        groups[label].append(row.index)
    total = sum(map(len, groups.values()))
    target = min(total, limit or total, math.ceil(total * fraction))
    rng = random.Random(str(cell.id))
    labels = sorted(groups)
    rng.shuffle(labels)
    for indices in groups.values():
        rng.shuffle(indices)
    # Give each stratum a place, then distribute the remaining budget in proportion.
    queue = [(0.0, order, 0, label) for order, label in enumerate(labels)]
    heapq.heapify(queue)
    selected = []
    while queue and len(selected) < target:
        _, order, count, label = heapq.heappop(queue)
        indices = groups[label]
        selected.append(indices[count])
        count += 1
        if count < len(indices):
            heapq.heappush(queue, (count / len(indices), order, count, label))
    return rows.rows(cell, sorted(selected))
