import asyncio
import base64
import hashlib
import json
import shlex
import time
from contextlib import contextmanager, suppress
from functools import partial
from uuid import UUID

import httpx
from asgiref.sync import sync_to_async
from django.core.files.base import ContentFile
from django.db import connections, transaction
from django.utils import timezone
from openai import APIConnectionError, APIStatusError
from temporalio import activity

from overbae.models import (
    DatasetPreparation,
    Project,
    WorkshopAction,
    WorkshopTask,
    WorkshopWorkspace,
)
from overbae.services.datasets import (
    partition_plans,
    paths,
    pipeline_packages,
    preparation_workflow,
    store,
    workbench,
)
from overbae.services.workshop_agent import (
    cache,
    conversation,
    lifecycle,
    model_stream,
    provisioning,
    workspace,
)
from workshop_runtime.data import iter_jsonl


def canonical(records):
    digest = hashlib.sha256(b"[")
    for index, record in enumerate(records):
        if index:
            digest.update(b",")
        digest.update(
            json.dumps(
                record, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode()
        )
    digest.update(b"]")
    return digest.hexdigest()


def call(task, operation, payload, *, sandbox=None):
    binding = WorkshopWorkspace.objects.get(project_id=task.project_id, enabled=True)
    grant = task.actions.get(sequence=0).receipt
    if (
        binding.task_owner != task.pk
        or grant.get("sandbox") != binding.sandbox_id
        or grant.get("generation") != binding.generation
        or (sandbox is not None and sandbox.id != binding.sandbox_id)
    ):
        raise ValueError("The native run belongs to a fenced workspace generation")
    sandbox = sandbox or workspace.attach(task, reconcile=True)
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    entry = "workshop_runtime.mailbox" if operation == "exchange" else "workshop_runtime.agent"
    operation_arg = "" if operation == "exchange" else f" {operation}"
    command = f"/opt/overmind-runtime/venv/bin/python -m {entry} --root {shlex.quote(workspace.task_root(task))}{operation_arg} --payload {shlex.quote(encoded)}"
    reply = sandbox.process.exec(command, timeout=60)
    if reply.exit_code:
        raise ValueError(f"Resident agent transport failed: {reply.result[-2000:]}")
    return json.loads(reply.result)


def fail_setup(task, message):
    with transaction.atomic():
        WorkshopTask.objects.filter(pk=task.pk).update(
            state="failed", result={"reason": message}, updated_at=timezone.now()
        )
        preparation_workflow.update(task.dataset, task.preparation_id, "cancelled", [])
        WorkshopWorkspace.objects.filter(project_id=task.project_id, task_owner=task.pk).update(
            task_owner=None
        )
        conversation.emit(
            task,
            "workspace-setup-failed",
            {"id": "workspace-setup", "role": "activity", "text": message, "status": "failed"},
        )


def bootstrap(task):
    lifecycle.authorise(task, require_workspace=False)
    if not WorkshopWorkspace.objects.filter(
        project=task.project, sandbox_id__isnull=False
    ).exists():
        message = "Setting up the project workspace"
        WorkshopTask.objects.filter(pk=task.pk).update(result={"activity": message})
        conversation.emit(
            task,
            "workspace-setup",
            {
                "id": "workspace-setup",
                "role": "activity",
                "text": message,
                "status": "running",
            },
        )
        try:
            ready = provisioning.ensure(task.project)
        except provisioning.WorkspaceSetupError as exc:
            fail_setup(task, str(exc))
            return None
        if not ready:
            return None
    if task.chat_events.filter(key="workspace-setup").exists():
        conversation.emit(
            task,
            "workspace-setup-ready",
            {
                "id": "workspace-setup",
                "role": "activity",
                "text": "Project workspace ready",
                "status": "completed",
            },
        )
    lifecycle.authorise(lifecycle.get(task.pk))
    with transaction.atomic():
        slot = WorkshopWorkspace.objects.select_for_update().get(project=task.project, enabled=True)
        if slot.task_owner not in (None, task.pk):
            return None
        slot.task_owner = task.pk
        slot.save(update_fields=["task_owner"])
    action, _ = lifecycle.claim(task.pk, 0, "prepare_workshop_environment", {})
    if action.state != "completed":
        try:
            result = workspace.prepare(task, action)
        except workspace.RuntimeUnavailableError as exc:
            with transaction.atomic():
                lifecycle.record_action(action.pk, "failed", {"error": str(exc)})
                fail_setup(task, str(exc))
            return None
        lifecycle.record_action(action.pk, "completed", result)
    elif task.retention:
        cache.restore(task)
    action.refresh_from_db()
    WorkshopAction.objects.filter(pk=action.pk).update(
        receipt={**action.receipt, "sandbox": slot.sandbox_id, "generation": slot.generation}
    )
    initial = (
        task.chat_events.filter(key="objective").first()
        or task.chat_events.filter(message__initial=True).first()
    )
    if initial:
        conversation.emit(
            task,
            "source-context",
            {
                **initial.message,
                "cells": initial.message.get("cells")
                or conversation.cell_refs(
                    task,
                    [
                        action.result["input"]["cell"],
                        *[source["cell"] for source in task.context_sources],
                    ],
                ),
            },
        )
    configuration = {
        "task": str(task.pk),
        "objective": task.objective,
        "model": model_stream.configured_model(),
        "generation": slot.generation,
        "history": conversation.history(task),
        "runtime_contracts": action.result["runtime_contracts"],
        "source_briefing": action.result["source_briefing"],
        "cell_context": initial.message.get("cells", []) if initial else [],
    }
    workspace.stage_chat_cells(task)
    return call(task, "launch", configuration)


@contextmanager
def replay_artifacts(task, replay_id, *, timings=None):
    timings = timings if timings is not None else {}
    replay_id = str(UUID(replay_id))
    sandbox = workspace.attach(task)
    root = workspace.task_root(task) + f"/.agent/replays/{replay_id}"
    receipt = json.loads(sandbox.fs.download_file(root + "/receipt.json"))
    if (
        receipt["state"] != "completed"
        or receipt["runtime"] != task.actions.get(sequence=0).result["runtime"]["identity"]
    ):
        raise ValueError("Native replay has not passed in the current runtime")
    names = [
        root + "/package.zip",
        *[root + f"/output-{index}.jsonl" for index in range(len(receipt["steps"]))],
    ]
    if receipt.get("partition_recipe"):
        names.append(root + "/assignments.jsonl")
    with workspace.download_replay_files(
        sandbox, names, timings=timings.setdefault("transfer", {})
    ) as files:
        verifying = time.monotonic()
        package = files[root + "/package.zip"].read_bytes()
        if hashlib.sha256(package).hexdigest() != receipt["package_sha256"]:
            raise ValueError("Replay package changed after execution")
        outputs = []
        for index, step in enumerate(receipt["steps"]):
            output = files[root + f"/output-{index}.jsonl"]
            if canonical(iter_jsonl(output)) != step["output_sha256"]:
                raise ValueError("Replay output changed after execution")
            with output.open(encoding="utf-8") as stream:
                count = sum(bool(line.strip()) for line in stream)
            if count != step["rows"]:
                raise ValueError("Replay output count does not match its receipt")
            outputs.append(output)
        if not outputs or receipt["steps"][-1]["rows"] != receipt["output_rows"]:
            raise ValueError("Replay final count does not match its receipt")
        assignments = files.get(root + "/assignments.jsonl")
        if assignments is not None:
            with assignments.open("rb") as stream:
                if (
                    hashlib.file_digest(stream, "sha256").hexdigest()
                    != receipt["assignments_sha256"]
                ):
                    raise ValueError("Partition assignments changed after execution")
        timings["verify_replay_seconds"] = time.monotonic() - verifying
        yield receipt, package, outputs, assignments


def publish(task, request):
    started = time.monotonic()
    timings = {}
    lifecycle.authorise(task)
    slot = WorkshopWorkspace.objects.get(project=task.project, task_owner=task.pk, enabled=True)
    grant = task.actions.get(sequence=0).receipt
    if grant.get("sandbox") != slot.sandbox_id or grant.get("generation") != slot.generation:
        raise ValueError("The native run belongs to a fenced workspace generation")
    with replay_artifacts(task, request["arguments"]["replay"], timings=timings) as artifacts:
        receipt, package, outputs, assignments = artifacts
        prepared = task.actions.get(sequence=0).result["input"]
        if prepared["sha256"] != receipt["source_sha256"]:
            raise ValueError("Replay is not bound to the pinned source")
        key = f"resident:{task.pk}:{request['id']}"
        with transaction.atomic():
            locked = WorkshopTask.objects.select_for_update().get(pk=task.pk)
            lifecycle.authorise(locked)
            bundle = pipeline_packages.save(
                task.project, task.created_by, ContentFile(package), runtime=receipt["runtime"]
            )
            pipeline = workbench.save_pipeline(
                task.project,
                task.created_by,
                name=request["arguments"]["name"],
                request_key=key,
                package=bundle.pk,
                dataset=task.dataset,
            )
            run = workbench.submit(
                task.dataset,
                task.created_by,
                source_cell=prepared["cell"],
                source_fingerprint=prepared["fingerprint"],
                request_key=key,
                pipeline=pipeline.pk,
                parameters=receipt["parameters"],
                provenance="Native resident agent; retained clean replay in Daytona",
                execution={
                    "kind": "native_workspace",
                    "runtime": receipt["runtime"],
                    "task": str(task.pk),
                    "replay": receipt["id"],
                },
            )

            def retained(run, index, source, directory, step, files, progress):
                actual = (
                    {
                        k: v
                        for k, v in row.items()
                        if not k.startswith("_overmind_") or k == "_overmind_parent_rows"
                    }
                    for row in store.iter_rows(source)
                )
                if canonical(actual) != receipt["steps"][index]["input_sha256"]:
                    raise ValueError(
                        "Replay step input does not match the independently reconstructed source"
                    )
                run.result["steps"][index].update(
                    runtime=receipt["runtime"],
                    native_replay=receipt["id"],
                    exit_code=receipt["steps"][index]["exit_code"],
                )
                yield from iter_jsonl(outputs[index])

            executing = time.monotonic()
            workbench.execute(run.pk, script_executor=retained)
            timings["pipeline_seconds"] = time.monotonic() - executing
            run.refresh_from_db()
            if run.state != "completed":
                raise ValueError(run.error or "Native publication validation failed")
            if run.output.rows != receipt["output_rows"]:
                raise ValueError("Published row count does not match the retained replay")
            result = {
                "cell": str(run.output_id),
                "fingerprint": run.output.fingerprint,
                "rows": run.output.rows,
                "pipeline": str(pipeline.pk),
                "run": str(run.pk),
                "replay": receipt["id"],
                "outputs": [str(run.output_id)],
                "cells": [step["output_cell"] for step in run.result["steps"]],
            }
            if receipt.get("partition_recipe"):
                partitioning = time.monotonic()
                plan = partition_plans.request_plan(
                    task.project,
                    name=request["arguments"]["name"],
                    request_key=key,
                    source_cell=run.output,
                    recipe=receipt["partition_recipe"],
                )
                partition_plans.build(plan.pk, assignment_file=assignments)
                plan.refresh_from_db()
                if plan.state != "completed":
                    raise ValueError(plan.error or "Native partition validation failed")
                if (
                    plan.report["counts"] != receipt["partition"]["counts"]
                    or plan.report["assignments_sha256"] != receipt["assignments_sha256"]
                ):
                    raise ValueError("Published partition does not match the retained replay")
                result["partition"] = partition_plans.describe(plan)
                result["outputs"].extend(
                    str(pk) for pk in plan.members.values_list("cell_id", flat=True)
                )
                result["cells"] = list(dict.fromkeys([*result["cells"], *result["outputs"]]))
                timings["partition_seconds"] = time.monotonic() - partitioning
            result["verified_against_replay"] = True
            timings["seconds"] = time.monotonic() - started
            result["timing"] = run.result.setdefault("native_publication_timing", timings)
            run.save(update_fields=["result"])
            return result


def relay(task, request, model):
    # The resident process selects actions; this boundary only supplies scoped external I/O.
    lifecycle.authorise(task)
    name = {"model": "reason", "publish": "publish_workshop", "present": "present_workshop"}.get(
        request["kind"], request["kind"]
    )
    sequence = 1 + request["turn"] * 2 + (request["kind"] != "model")
    action, created = lifecycle.claim(
        task.pk,
        sequence,
        name,
        {"request": request["id"], "payload_sha256": lifecycle.digest(request["arguments"])},
    )
    if action.state == "completed":
        return action.receipt["response"]
    if action.state in {"intent", "unknown"} and not created and request["kind"] == "model":
        return {
            "unknown": True,
            "error": "Model acknowledgement lost; the same request was not resubmitted",
        }
    try:
        if request["kind"] == "model":
            configured = model_stream.configured_model()
            if request["arguments"].get("model") != configured or request["arguments"].get(
                "stream"
            ):
                raise ValueError("Model request exceeds this task's inference scope")
            result = model.complete(task, request)
        elif request["kind"] == "present":
            result = conversation.present(task, request["turn"], request["arguments"])
        elif request["kind"] == "publish":
            result = publish(task, request)
            conversation.emit(
                task,
                f"publication:{request['turn']}",
                {
                    "id": f"publication:{request['turn']}",
                    "role": "publication",
                    "text": request["arguments"]["name"],
                    "status": "completed",
                    "cells": conversation.cell_refs(task, result["cells"]),
                },
            )
        else:
            raise ValueError("Unsupported resident external operation")
    except (httpx.TransportError, APIConnectionError, model_stream.UnknownResponseError):
        lifecycle.record_action(action.pk, "unknown", {"error": "provider_acknowledgement_unknown"})
        return {"unknown": True}
    except APIStatusError as exc:
        result = {
            "status": exc.status_code,
            "body": {"error": {"message": "The model provider rejected this request."}},
        }
    except ValueError as exc:
        result = {"error": str(exc)}
    with transaction.atomic():
        WorkshopAction.objects.filter(pk=action.pk).update(
            state="completed",
            receipt={
                "response": result,
                "usage_known": isinstance(
                    (result.get("body", {}).get("usage") or {}).get("total_tokens"), int
                )
                if request["kind"] == "model"
                else True,
            },
            result={k: v for k, v in result.items() if k != "body"},
            updated_at=timezone.now(),
        )
        if request["kind"] == "model":
            usage = (result.get("body") or {}).get("usage")
            if usage and isinstance(usage.get("total_tokens"), int):
                known = task.actions.filter(name="reason", state="completed").values_list(
                    "receipt", flat=True
                )
                total = sum(
                    (r.get("response", {}).get("body", {}).get("usage") or {}).get(
                        "total_tokens", 0
                    )
                    for r in known
                )
                WorkshopTask.objects.filter(pk=task.pk).update(used_tokens=total)
    return result


def project_state(task, snapshot):
    state = snapshot["state"]
    # The checkpoint is a durable recovery copy, separate from the Daytona disk.
    directory = paths.media_root() / "workshop-checkpoints" / str(task.pk)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / "checkpoint.tmp"
    temporary.write_text(json.dumps(state))
    temporary.replace(directory / "checkpoint.json")
    if state["state"] in {"completed", "blocked", "cancelled"}:
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=task.project_id)
            current = (
                WorkshopTask.objects.select_for_update(of=("self",))
                .select_related("dataset", "preparation")
                .get(pk=task.pk)
            )
            unread = {message["id"] for message in conversation.inbox(current)} - set(
                state.get("accepted_messages", [])
            )
            if (
                unread
                and current.desired_state == "running"
                and state["state"] in {"completed", "blocked"}
            ):
                events = state.get("events", [])
                if events and events[-1]["tool"] == "finish" and not events[-1].get("error"):
                    return {"state": "continuing", "turn": state["turn"]}
            conversation.project_snapshot(task, state)
            outputs = list(
                dict.fromkeys(
                    cell
                    for publication in state.get("publications", [])
                    for cell in publication.get("outputs", [])
                )
            )
            if state["state"] == "completed":
                lifecycle.authorise(current)
                primary = lifecycle.completion_outputs(current, outputs) if outputs else []
                if not outputs and not state.get("answered"):
                    raise ValueError(
                        "Completion requires a verified publication or an answered question"
                    )
                preparation_workflow.update(
                    current.dataset,
                    current.preparation_id,
                    "completed" if primary else "cancelled",
                    primary,
                )
            elif current.preparation.state not in {"completed", "cancelled"}:
                preparation_workflow.update(
                    current.dataset,
                    current.preparation_id,
                    "cancelled" if state["state"] == "cancelled" else "paused",
                    [],
                )
            current.state = state["state"]
            current.result = {
                "reason": state.get("reason", ""),
                "outputs": outputs,
                "resident_turns": state["turn"],
                "events": state.get("events", []),
                "activity": state.get("activity"),
            }
            current.save(update_fields=["state", "result", "updated_at"])
            events = state.get("events", [])
            settled_clarification = (
                current.state == "blocked"
                and snapshot.get("alive") is False
                and snapshot.get("pending") == []
                and events
                and events[-1].get("tool") == "finish"
                and events[-1].get("error") is None
                and not current.actions.filter(state__in=["intent", "unknown"]).exists()
            )
            # A clarification preserves its task, but only a settled runner can yield the slot.
            if current.state in lifecycle.TERMINAL or settled_clarification:
                WorkshopWorkspace.objects.filter(
                    project_id=current.project_id, task_owner=current.pk
                ).update(task_owner=None)
    else:
        conversation.project_snapshot(task, state)
        desired_preparation = "preparing" if task.desired_state == "running" else "paused"
        if task.preparation.state != desired_preparation:
            preparation_workflow.update(task.dataset, task.preparation_id, desired_preparation, [])
        elif desired_preparation == "preparing":
            DatasetPreparation.objects.filter(pk=task.preparation_id, state="preparing").update(
                updated_at=timezone.now()
            )
        WorkshopTask.objects.filter(pk=task.pk).update(
            result={
                "resident_turns": state.get("turn", 0),
                "events": state.get("events", [])[-20:],
                "activity": state.get("activity"),
            },
            state="running" if task.desired_state == "running" else "paused",
            step=state.get("turn", 0),
            updated_at=timezone.now(),
        )
    return {"state": state["state"], "turn": state.get("turn")}


async def run_supervisor(task_id: str) -> dict:
    with model_stream.ModelStream() as model:
        return await supervise_task(task_id, model)


async def supervise_task(task_id, model):
    async def db(function, *args):
        return await sync_to_async(function, thread_sensitive=True)(*args)

    task = await db(lifecycle.get, task_id)
    if task.state in lifecycle.TERMINAL:
        return {"state": task.state}
    prepared = await db(task.actions.filter(sequence=0, state="completed").exists)
    if prepared and task.retention:
        await db(cache.restore, task)
    if not prepared and task.desired_state != "running":
        state = "cancelled" if task.desired_state == "cancelled" else "paused"
        await db(project_state, task, {"state": {"state": state, "turn": 0}})
        return {"state": state}
    snapshot = (
        await db(call, task, "exchange", {"control": task.desired_state})
        if prepared
        else await db(bootstrap, task)
    )
    if snapshot is None:
        current = await db(lifecycle.get, task_id)
        return {"state": current.state if current.state in lifecycle.TERMINAL else "queued"}
    # Hold one transport for the leased workspace; call checks the live binding on every exchange.
    sandbox = await db(partial(workspace.attach, reconcile=True), task)
    remote_call = partial(call, sandbox=sandbox)
    while True:
        activity.heartbeat({"task": task_id, "turn": snapshot["state"].get("turn")})
        task = await db(lifecycle.get, task_id)
        if task.preparation.state == "cancelled":
            await sync_to_async(
                WorkshopTask.objects.filter(pk=task.pk).update, thread_sensitive=True
            )(desired_state="cancelled")
            task.desired_state = "cancelled"
        responses = {}
        if task.desired_state == "running":
            for request in snapshot.get("pending", []):
                # Keep control delivery independent of a blocked provider stream.
                response = asyncio.create_task(
                    asyncio.to_thread(relay_with_connections, task, request, model)
                )
                sent_control = task.desired_state
                while not response.done():
                    await asyncio.wait({response}, timeout=1)
                    activity.heartbeat({"task": task_id, "external": request["kind"]})
                    current = await db(lifecycle.get, task_id)
                    if current.desired_state != sent_control:
                        await db(
                            remote_call, current, "exchange", {"control": current.desired_state}
                        )
                        sent_control = current.desired_state
                retained = response.result()
                if not retained.get("cancelled"):
                    responses[request["id"]] = retained
                task = await db(lifecycle.get, task_id)
                if task.desired_state != "running":
                    break
        await db(workspace.stage_chat_cells, task)
        snapshot = await db(
            remote_call,
            task,
            "exchange",
            {
                "control": task.desired_state,
                "responses": responses,
                "wait": 1 if task.desired_state == "running" else 0,
                "messages": await db(conversation.claim_inbox, task),
            },
        )
        if not snapshot["alive"] and task.desired_state == "cancelled":
            snapshot["state"]["state"] = "cancelled"
        result = await db(project_state, task, snapshot)
        if result["state"] in {"completed", "cancelled", "blocked"}:
            return result
        if not snapshot["alive"] and task.desired_state == "running":
            snapshot = await db(bootstrap, task)
        elif not snapshot["alive"] and task.desired_state == "cancelled":
            return await db(
                project_state, task, {"state": {**snapshot["state"], "state": "cancelled"}}
            )
        if task.desired_state != "running":
            await asyncio.sleep(2)


def relay_with_connections(task, request, model):
    try:
        return relay(task, request, model)
    finally:
        connections.close_all()


@activity.defn
async def supervise_run(task_id: str) -> dict:
    async def heartbeat():
        while True:
            activity.heartbeat({"task": task_id})
            await asyncio.sleep(5)

    pulse = asyncio.create_task(heartbeat())
    try:
        return await run_supervisor(task_id)
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


@activity.defn
async def fence_run(task_id: str) -> dict:
    await sync_to_async(
        WorkshopTask.objects.filter(pk=task_id).exclude(state__in=lifecycle.TERMINAL).update,
        thread_sensitive=True,
    )(
        state="blocked",
        desired_state="paused",
        result={
            "reason": "The coordinator could not reconcile the resident run. Its workspace and effects remain retained; inspect the saved receipts before recovery."
        },
        updated_at=timezone.now(),
    )
    return {"state": "blocked"}
