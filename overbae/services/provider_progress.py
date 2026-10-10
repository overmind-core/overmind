import asyncio
import logging
import math
from datetime import timedelta

import modal
from asgiref.sync import async_to_sync
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from modal_shared.modelfam import serve_image_key
from modal_shared.operational_events import STORE, pool_key
from modal_shared.shared import rel_weights_path, worker_cls_name
from overbae.models import OperationalEvent, OperationalRun
from overbae.services import operational_progress

logger = logging.getLogger(__name__)


async def read_page(environment, stream, cursor):
    store = modal.Dict.from_name(STORE, environment_name=environment)
    head = await store.get.aio(stream, None)
    if not head:
        return None, []
    latest_writer = head["writer"]
    gap = False
    if cursor.get("writer") and cursor["writer"] != head["writer"]:
        chain = [head]
        for _ in range(50):
            candidate = chain[-1]
            if candidate["writer"] == cursor["writer"]:
                head = candidate if cursor["sequence"] < candidate["sequence"] else chain[-2]
                break
            previous = candidate.get("previous")
            candidate = await store.get.aio(f"{previous}:head", None) if previous else None
            if candidate is None:
                gap = True
                break
            chain.append(candidate)
        else:
            gap = True
    writer = head["writer"]
    after = cursor.get("sequence", 0) if cursor.get("writer") == writer else 0
    end = min(int(head["sequence"]), after + 50)
    events = await asyncio.gather(
        *(store.get.aio(f"{writer}:{i}", None) for i in range(after + 1, end + 1))
    )
    return {
        "writer": writer,
        "sequence": end,
        "available_through": head["sequence"],
        "dropped": head.get("dropped", 0),
        "publication_failures": head.get("publication_failures"),
        "history_gap": gap or any(event is None for event in events),
        "missing_events": sum(event is None for event in events),
        "has_more": end < int(head["sequence"]) or writer != latest_writer,
    }, events


async def bounded_page(environment, stream, cursor):
    return await asyncio.wait_for(read_page(environment, stream, cursor), timeout=8)


def collect(run, *, environment, call_id="", deployed=None):
    now = timezone.now()
    collection = {
        "environment": environment,
        "call_id": call_id,
        "deployment_id": str(deployed.pk) if deployed else None,
        "until": (
            run.collection.get("until")
            if run.snapshot.get("status") in operational_progress.TERMINAL
            else None
        )
        or (now + timedelta(hours=1)).isoformat(),
    }
    OperationalRun.objects.filter(pk=run.pk).update(
        collection=collection, next_collection_at=now + timedelta(minutes=1)
    )
    run.collection = collection
    incomplete = False
    graph = cache.get("operation-graph:" + call_id) if call_id else None
    if graph:
        with transaction.atomic():
            current = OperationalRun.objects.select_for_update().get(pk=run.pk)
            previous = current.provider_state.get("call_graph", {})
            if graph.get("calls") != previous.get("calls"):
                current.sequence += 1
                OperationalEvent.objects.create(
                    operation=current,
                    sequence=current.sequence,
                    event="provider_graph",
                    facts=graph,
                    source_at=operational_progress.timestamp(graph["observed_at"]),
                    observed_at=timezone.now(),
                )
            current.provider_state = {**current.provider_state, "call_graph": graph}
            current.save(update_fields=["provider_state", "sequence"])
            run.provider_state = current.provider_state
    streams = (
        [("call", "call:" + call_id), ("request", "worker-call:" + call_id)] if call_id else []
    )
    if deployed and deployed.weights_path and deployed.gpu_type:
        lora = bool(deployed.adapter_path)
        cls_name = worker_cls_name(
            deployed.gpu_type,
            serve_image_key(deployed.base_model_id, deployed.model_id),
            enable_lora=lora,
        )
        streams.append(
            (
                "pool",
                pool_key(
                    cls_name,
                    rel_weights_path(deployed.weights_path),
                    deployed.max_model_len,
                    lora,
                    (deployed.lora_rank or 16) if lora else 16,
                ),
            )
        )
    for scope, stream in streams:
        try:
            cursor = (run.provider_state.get(scope) or {}).get("cursor", {})
            head, events = async_to_sync(bounded_page)(environment, stream, cursor)
            incomplete = incomplete or bool(head and head.get("has_more"))
            with transaction.atomic():
                current = OperationalRun.objects.select_for_update().get(pk=run.pk)
                state = dict(current.provider_state)
                previous = state.get(scope) or {}
                if head:
                    saved_cursor = previous.get("cursor", {})
                    if saved_cursor != cursor:
                        continue
                    head["history_gap"] = bool(
                        head.get("history_gap") or saved_cursor.get("history_gap")
                    )
                    head["missing_events"] = head.get("missing_events", 0) + saved_cursor.get(
                        "missing_events", 0
                    )
                    for raw in events:
                        if raw is None:
                            continue
                        if not isinstance(raw, dict):
                            raise ValueError("Provider journal event missing")
                        facts = operational_progress.facts_only(raw.get("facts"))
                        for count in (raw.get("completed"), raw.get("total")):
                            if count is not None and (
                                type(count) not in (int, float)
                                or not math.isfinite(count)
                                or count < 0
                            ):
                                raise ValueError("Invalid provider measurement")
                        source_at = operational_progress.timestamp(raw.get("source_at"))
                        if source_at is None:
                            raise ValueError("Provider event has no timestamp")
                        value = {
                            "stage": str(raw["stage"])[:80],
                            "completed": raw.get("completed"),
                            "total": raw.get("total"),
                            "unit": raw.get("unit"),
                            "facts": facts,
                            "scope": "shared_pool" if scope == "pool" else "operation",
                            "provider_call_id": call_id if scope != "pool" else None,
                        }
                        current.sequence += 1
                        OperationalEvent.objects.create(
                            operation=current,
                            sequence=current.sequence,
                            event="provider",
                            facts=value,
                            source_at=source_at,
                            observed_at=timezone.now(),
                        )
                        # Pool startup may predate this operation; it is context, not its progress.
                        if (
                            scope != "pool"
                            and source_at >= current.created_at
                            and (
                                not current.last_progress_at or source_at > current.last_progress_at
                            )
                            and operational_progress.forward_change(previous, value)
                        ):
                            current.last_progress_at = source_at
                        previous = {**previous, **value, "source_at": source_at.isoformat()}
                        if facts.get("process_alive") is True:
                            previous["last_heartbeat_at"] = source_at.isoformat()
                    previous.pop("reason", None)
                    state[scope] = {
                        **previous,
                        "cursor": head,
                        "available": True,
                        "observed_at": timezone.now().isoformat(),
                    }
                else:
                    state[scope] = {**previous, "available": False, "reason": "not_reported"}
                current.provider_state = state
                current.save(update_fields=["sequence", "provider_state", "last_progress_at"])
                run.provider_state = state
        except Exception:
            incomplete = True
            logger.warning(
                "Provider telemetry unavailable for operation %s", run.pk, exc_info=False
            )
            with transaction.atomic():
                current = OperationalRun.objects.select_for_update().get(pk=run.pk)
                state = dict(current.provider_state)
                state[scope] = {
                    **state.get(scope, {}),
                    "available": False,
                    "reason": "observation_unavailable",
                }
                current.provider_state = state
                current.save(update_fields=["provider_state"])
    OperationalRun.objects.filter(pk=run.pk).update(
        next_collection_at=(
            timezone.now() + timedelta(minutes=1)
            if incomplete and operational_progress.timestamp(collection["until"]) > timezone.now()
            else None
        )
    )
