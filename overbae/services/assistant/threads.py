"""What a user does to a conversation: open it, send a message, change the queue."""

import json

from django.db import transaction
from django.db.models import F, Max

from overbae.core.errors import InputValidationError
from overbae.models import AssistantInput, AssistantThread, Cell, Dataset
from overbae.services.assistant import log
from overbae.services.codebase.flow import build_capability_flow
from overbae.services.datasets import alignment, rows, store

Queued = AssistantInput.State.QUEUED
# A wide dataset names its first columns; the model queries the rest.
BRIEF_COLUMNS = 40
TOP_VALUE_CHARS = 80
CONTRACT_CHARS = 4000
CAPABILITY_FIELDS = (
    "description",
    "system_prompt",
    "input_schema",
    "output_fields",
    "expected_output",
    "success_criteria",
    "failure_modes",
    "vocabulary",
)


def open_thread(dataset: Dataset) -> AssistantThread:
    """The dataset's conversation, made on first use."""
    return AssistantThread.objects.get_or_create(
        dataset=dataset, defaults={"project_id": dataset.project_id}
    )[0]


def data_name(cell) -> str:
    """The file a version has in a workspace's ``data``. A published version never changes,
    so its id names its rows."""
    return f"{cell.pk}.parquet"


def brief(thread) -> dict:
    """What the dataset holds now, in JSON types: the model reads it with the user's
    message, so its first step is work and not a lookup."""
    dataset = thread.dataset
    names = dataset.versions()
    source, active = dataset.source, dataset.active_cell

    def version(cell):
        return {
            "cell": str(cell.pk),
            "title": cell.title,
            "version": names.get(cell.pk),
            "rows": cell.rows,
            "path": f"data/{data_name(cell)}",
            "fit": cell.fit(),
            **_profile(cell),
        }

    out = {
        "dataset": {
            "id": str(dataset.pk),
            "name": dataset.name,
            "intent": dataset.intent,
            "train_format": dataset.train_format,
        },
        "versions": len(names),
    }
    if dataset.capability_id is not None:
        out["capability"] = capability_contract(dataset.capability)
    if source is not None and source.ran:
        out["source"] = version(source)
    if active is not None and active != source:
        out["latest"] = version(active)
    recipe = dataset.preparation_pipeline
    if recipe is not None:
        out["pipeline"] = {"id": str(recipe.family), "name": recipe.name}
    split = (dataset.source_spec or {}).get("split") or {}
    sibling = Dataset.objects.filter(pk=split.get("sibling"), project=dataset.project_id).first()
    if sibling is not None:
        out["split"] = {
            "role": split.get("role"),
            "sibling": {
                "id": str(sibling.pk),
                "name": sibling.name,
                "intent": sibling.intent,
                "source_cell": str(sibling.source.pk) if sibling.source else None,
            },
        }
    return out


def _profile(cell) -> dict:
    """Each column's kind, nulls, examples and, from a fixed sample, its distinct and most
    common values; and the version's exact duplicate rows."""
    try:
        measured = store.frame_profile(rows.frame_path(cell), fingerprint=cell.fingerprint)
    except (rows.RowStoreError, store.StoreError):
        measured = None
    by_name = {column["name"]: column for column in (measured or {}).get("columns", [])}
    columns = []
    for column in cell.columns[:BRIEF_COLUMNS]:
        stats = by_name.get(column["name"], {})
        columns.append(
            {
                key: value
                for key, value in {
                    "name": column["name"],
                    "type": column.get("type"),
                    "null_rate": round(column.get("null_rate", 0.0), 4),
                    "examples": column.get("examples", []),
                    "distinct": stats.get("distinct"),
                    "top": [
                        {"value": str(top["value"])[:TOP_VALUE_CHARS], "count": top["count"]}
                        for top in stats.get("top", [])
                    ]
                    or None,
                    **{k: stats.get(k) for k in ("min", "max", "mean", "mean_len", "max_len")},
                }.items()
                if value is not None
            }
        )
    out = {"column_count": len(cell.columns), "columns": columns}
    if measured is None:
        out["profile"] = "unavailable"
        return out
    sample = next(iter(by_name.values()), {})
    out["duplicate_rows"] = measured["duplicate_rows"]
    out["profile_sample_rows"] = sample.get("sample_rows", measured["rows"])
    return out


def capability_contract(capability) -> dict:
    """What the capability takes and returns, as its card states it, bounded."""
    card = build_capability_flow(capability)
    out = {"name": capability.name}
    for field in CAPABILITY_FIELDS:
        value = {
            "description": capability.description,
            "system_prompt": alignment.system_prompt(capability),
        }.get(field, card.get(field))
        if not value:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        out[field] = value if len(text) <= CONTRACT_CHARS else text[:CONTRACT_CHARS]
    tools = [
        t["name"] for t in card.get("tool_spec") or [] if isinstance(t, dict) and t.get("name")
    ]
    if tools:
        out["tools"] = tools
    return out


FIRST_PASS = {
    Dataset.Intent.TRAIN: "Prepare this dataset for training.",
    Dataset.Intent.EVAL: "Prepare this dataset for evaluation.",
    Dataset.Intent.PENDING: "Prepare this dataset for the use its rows fit best.",
}
SPLIT_PASS = "Prepare this dataset for training and its evaluation split for evaluation."


def queue_first_pass(dataset: Dataset, user, *, split: bool = False) -> AssistantInput:
    """The first message of a dataset made with an intent or a brief: its turn starts once
    the source lands. A split prepares both halves in one conversation, with one script."""
    text = SPLIT_PASS if split else FIRST_PASS[dataset.intent]
    if dataset.brief.strip():
        text = f"{text}\n\n{dataset.brief.strip()}"
    return send(open_thread(dataset), user, request_key=f"first-pass:{dataset.pk}", text=text)


def send(thread, user, *, request_key, text, cells=(), interaction=None) -> AssistantInput:
    """Queue a message. The same key with the same message returns the first one."""
    cells = [str(cell) for cell in cells]
    with transaction.atomic():
        AssistantThread.objects.select_for_update().get(pk=thread.pk)
        prior = thread.inputs.filter(request_key=request_key).first()
        if prior is not None:
            if (prior.text, prior.cells, prior.interaction) != (text, cells, interaction):
                raise InputValidationError("This request key identifies a different message.")
            return prior
        known = Cell.objects.filter(dataset__project=thread.project_id, pk__in=cells).count()
        if known != len(set(cells)):
            raise InputValidationError("A referenced dataset version is not in this project.")
        last = thread.inputs.aggregate(last=Max("position"))["last"] or 0
        log.changed(thread)
        return AssistantInput.objects.create(
            thread=thread,
            created_by=user,
            request_key=request_key,
            text=text,
            cells=cells,
            interaction=interaction,
            position=last + 1,
        )


def change(message_id, thread, user, *, revision, action, text=None, before=None) -> AssistantInput:
    """Edit, remove, move or send now a queued message. ``revision`` must be the one the
    user saw, so a change to a message a turn already read is refused. A message's words
    are its sender's: only they edit or remove it; any member orders the queue."""
    with transaction.atomic():
        # The thread first, as ``send`` does: two moves then cannot lock each other's rows.
        AssistantThread.objects.select_for_update().get(pk=thread.pk)
        message = (
            AssistantInput.objects.select_for_update().filter(pk=message_id, thread=thread).first()
        )
        if message is None or message.state != Queued or message.revision != revision:
            raise InputValidationError("This message changed or was already sent.")
        if action in ("edit", "remove") and message.created_by_id != user.pk:
            raise InputValidationError("Only its sender can edit or remove a queued message.")
        if action == "edit":
            message.text = text
        elif action == "remove":
            message.state = AssistantInput.State.REMOVED
        elif action == "send_now":
            message.urgent = True
        elif action == "move":
            queue = [m for m in thread.inputs.filter(state=Queued) if m.pk != message.pk]
            index = next((i for i, m in enumerate(queue) if str(m.pk) == str(before)), len(queue))
            queue.insert(index, message)
            for position, moved in enumerate(queue, start=1):
                AssistantInput.objects.filter(pk=moved.pk).update(position=position)
            message.position = index + 1
        message.revision = F("revision") + 1
        message.save()
        log.changed(thread)
    message.refresh_from_db()
    return message
