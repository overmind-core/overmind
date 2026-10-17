"""Opt-in capacity experiment: real HTTP, database, broker and sandbox execution."""

import json
import math
import os
import platform
import resource
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path

import pytest
import requests
from celery.contrib.testing.worker import start_worker
from celery.signals import before_task_publish, task_postrun
from django.db import close_old_connections, connection
from factories import make_capability, make_project, make_user
from journeys.stack import TaskLedger
from journeys.surfaces import McpSurface
from journeys.test_live_scoring import Trace

from overbae.celery import app
from overbae.models import (
    APIToken,
    Dataset,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceRequest,
    Span,
    Subscription,
    TrainingPreparation,
)
from overbae.services.datasets import pipeline_runner
from overbae.tasks.finetuning_reconciler import reconcile_finetuning_jobs
from overbae.tasks.inference_requests import reconcile_inference_requests
from overbae.tasks.training_preparation import reconcile as reconcile_preparation
from tests.load.processes import ProcessWorker, drain_process_results
from tests.workshop_script_fixtures import identity_script, retained_package

pytestmark = pytest.mark.django_db(transaction=True)


def percentiles(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        **{
            name: ordered[math.ceil(len(ordered) * fraction) - 1]
            for name, fraction in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99))
        },
    }


def test_mixed_large_payload_soak(live_api, settings, fake_llm, fake_modal, sft):
    fake_llm.extra_models.append("fake/candidate")
    fake_llm.on(lambda request: request.model == "fake/candidate", "Answer")

    def decision(*_):
        time.sleep(0.05)
        return "yes"

    fake_llm.decide = decision
    seconds = int(os.environ.get("OVERMIND_SOAK_SECONDS", "30"))
    clients = int(os.environ.get("OVERMIND_SOAK_CLIENTS", "8"))
    rows = int(os.environ.get("OVERMIND_SOAK_ROWS", "8192"))
    landing_slots = int(os.environ.get("OVERMIND_SOAK_LANDING_SLOTS", "1"))
    report = {
        "seconds": seconds,
        "clients": clients,
        "rows_per_large_upload": rows,
        "host": {"platform": platform.platform(), "cpus": os.cpu_count()},
        "worker_pool": {
            "interactive": "separate prefork",
            "landing": "separate prefork",
            "other_profiles": "threads",
        },
        "worker_slots": {
            "control": 2,
            "io": 4,
            "io_traces": 4,
            "batch": 2,
            "landing": landing_slots,
            "interactive": 4,
        },
        "per_project_pipeline_limit": settings.WORKSHOP_PROJECT_CONCURRENCY,
    }
    fake_modal.deploy(
        "overmind-inference",
        "inference_request",
        lambda *_: {
            "content": "Answer",
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "finish_reason": "stop",
            "latency_ms": 1,
        },
    )
    identities = []
    capabilities = []
    for index in range(9):
        user = make_user()
        if index == 0:
            Subscription.objects.create(user=user, status="active")
        project = make_project(name=f"mixed-{index}", member=user)
        key, _ = APIToken.create_for_user(user, project=project)
        identities.append((project, user, key))
        capabilities.append(make_capability(project, with_set=True))

    def request(index, method, path, **kwargs):
        response = requests.request(
            method,
            live_api.url + path,
            headers={"X-Api-Key": identities[index][2]},
            timeout=60,
            **kwargs,
        )
        assert response.status_code < 400, (response.status_code, response.text[:500])
        return response.json()

    def read(index):
        started = time.monotonic()
        result = request(index, "GET", "/api/projects/")
        assert str(identities[index][0].pk) in json.dumps(result)
        return time.monotonic() - started

    def upload(index, count, width):
        content = b"input,expected_output\n" + b"".join(
            f"question-{row}-{'x' * width},answer-{row}\n".encode() for row in range(count)
        )
        reserved = request(index, "POST", "/api/uploads/", json={"filename": "load.csv"})
        upload_id = reserved["upload_id"]
        chunk = 1024 * 1024
        for offset in range(0, len(content), chunk):
            request(
                index,
                "PUT",
                f"/api/uploads/{upload_id}/chunk/?offset={offset}",
                data=content[offset : offset + chunk],
            )
        inspected = request(
            index, "POST", f"/api/uploads/{upload_id}/inspect/", json={"size": len(content)}
        )
        assert inspected["rows"] == count
        result = request(
            index,
            "POST",
            "/api/datasets/",
            json={
                "project": str(identities[index][0].pk),
                "name": f"load-{uuid.uuid4().hex}",
                "intent": "explore",
                "source": {"uploads": [upload_id]},
            },
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            dataset = Dataset.objects.get(pk=result["id"])
            if dataset.active_cell:
                assert dataset.active_cell.rows == count
                return dataset, len(content)
            time.sleep(0.1)
        raise AssertionError(f"landing did not complete: {dataset.operation}")

    pipeline_receipts = []

    def pipeline(index, dataset, recipe):
        mcp = McpSurface(live_api.url, identities[index][2])
        started = time.monotonic()
        arguments = {
            "dataset": str(dataset.pk),
            "pipeline": recipe["id"],
            "source_cell": str(dataset.active_cell.pk),
            "source_fingerprint": dataset.active_cell.fingerprint,
            "request_key": uuid.uuid4().hex,
        }
        receipt = mcp.call("run_dataset_pipeline", arguments)["run"]
        deadline = started + 120
        while time.monotonic() < deadline:
            job = mcp.call("get_job", {"kind": "dataset_pipeline", "id": receipt["id"]})
            if job["status"] in ("completed", "failed", "cancelled"):
                assert job["status"] == "completed", job
                progress = job.get("progress", {})
                pipeline_receipts.append(
                    {
                        "quiet": index == 0,
                        "id": job["id"],
                        "elapsed_seconds": time.monotonic() - started,
                        "queue_seconds": progress.get("queue_seconds"),
                        "execution_seconds": progress.get("seconds"),
                        "stage_seconds": progress.get("stage_seconds"),
                    }
                )
                if index == 1:
                    overlap["pipelines"].append((started, time.monotonic()))
                return time.monotonic() - started
            time.sleep(0.1)
        raise AssertionError(f"pipeline did not finish: {job}")

    stop = threading.Event()
    errors, counts, large_bytes, pipeline_times, eval_times, trace_times, inference_times = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    preparation_times = []
    training_times = []
    overlap = {
        name: []
        for name in ("training", "imports", "pipelines", "traces", "inference", "quiet_reads")
    }
    oversized_queries = []

    def reader(index):
        count = 0
        try:
            while not stop.is_set():
                read(1 + index % 8)
                count += 1
                stop.wait(0.02)
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            counts.append(count)
            close_old_connections()

    def writer():
        try:
            while not stop.is_set():
                began = time.monotonic()
                dataset, size = upload(1, rows, 1024)
                large_bytes.append(size)
                pipeline_times.append(pipeline(1, dataset, recipes[1]))
                overlap["imports"].append((began, time.monotonic()))
                started = time.monotonic()
                with pytest.raises(AssertionError, match="query_result_too_large"):
                    McpSurface(live_api.url, identities[1][2]).call(
                        "query_dataset",
                        {
                            "dataset": str(dataset.pk),
                            "sql": "select * from t limit 100",
                        },
                    )
                oversized_queries.append(time.monotonic() - started)
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            close_old_connections()

    def agent_work():
        try:
            dataset = request(
                1,
                "POST",
                "/api/datasets/",
                json={
                    "project": str(identities[1][0].pk),
                    "name": "evaluation load",
                    "intent": "eval",
                    "capability": str(capabilities[1].pk),
                    "source": {
                        "rows": [
                            {
                                "input": {
                                    "messages": [{"role": "user", "content": f"Question {index}"}]
                                },
                                "expected_output": "Answer",
                            }
                            for index in range(4)
                        ]
                    },
                },
            )
            deadline = time.monotonic() + 30
            while not Dataset.objects.get(pk=dataset["id"]).active_cell:
                assert time.monotonic() < deadline
                time.sleep(0.1)
            mcp = McpSurface(live_api.url, identities[1][2])
            while not stop.is_set():
                started = time.monotonic()
                evaluation = mcp.call(
                    "run_evaluation",
                    {
                        "name": "soak",
                        "dataset": dataset["id"],
                        "judge_model": "gpt-5.6-terra",
                        "targets": [
                            {
                                "mode": "generate",
                                "model_name": "fake/candidate",
                                "label": "candidate",
                            }
                        ],
                    },
                )
                run_ids = [run["id"] for run in evaluation["runs"]]
                traces = []
                for index in range(9):
                    trace = Trace(
                        str(capabilities[index].pk), f"Answer {uuid.uuid4().hex}", tools=[True]
                    )
                    trace.send(live_api.url, identities[index][2])
                    traces.append(trace.hex)
                deadline = started + 60
                while (
                    EvalRun.objects.filter(pk__in=run_ids)
                    .exclude(status__in=EvalRun.TERMINAL)
                    .exists()
                ):
                    assert time.monotonic() < deadline, "evaluation stalled"
                    time.sleep(0.1)
                assert set(
                    EvalRun.objects.filter(pk__in=run_ids).values_list("status", flat=True)
                ) == {"completed"}
                eval_times.append(time.monotonic() - started)
                while Span.objects.filter(
                    trace_id__in=traces, parent_span_id__isnull=True, eval_attributes={}
                ).exists():
                    assert time.monotonic() < deadline, "live scoring stalled"
                    time.sleep(0.1)
                trace_times.append(time.monotonic() - started)
                overlap["traces"].append((started, time.monotonic()))
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            close_old_connections()

    def inference_work():
        try:
            deployment = DeployedModel.objects.create(
                project=identities[0][0],
                model_id="ft-soak",
                status="ready",
                base_model_id="Qwen/Qwen3-1.7B",
                max_model_len=16384,
            )
            mcp = McpSurface(live_api.url, identities[0][2])
            while not stop.is_set():
                started = time.monotonic()
                job = mcp.call(
                    "run_inference",
                    {
                        "deployment": str(deployment.pk),
                        "request_key": uuid.uuid4().hex,
                        "messages": [{"role": "user", "content": "Question"}],
                        "max_tokens": 32,
                    },
                )["job"]
                deadline = started + 45
                while InferenceRequest.objects.get(pk=job["id"]).state not in (
                    "succeeded",
                    "failed",
                ):
                    assert time.monotonic() < deadline, "inference collection stalled"
                    reconcile_inference_requests.delay()
                    time.sleep(1)
                assert InferenceRequest.objects.get(pk=job["id"]).state == "succeeded"
                inference_times.append(time.monotonic() - started)
                overlap["inference"].append((started, time.monotonic()))
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            close_old_connections()

    def preparation_work():
        try:
            mcp = McpSurface(live_api.url, identities[0][2])
            while not stop.is_set():
                dataset = request(
                    0,
                    "POST",
                    "/api/datasets/",
                    json={
                        "project": str(identities[0][0].pk),
                        "name": "preparation load",
                        "intent": "train",
                        "source": {
                            "rows": [
                                {
                                    "messages": [
                                        {"role": "user", "content": f"Question {index}"},
                                        {"role": "assistant", "content": "Answer"},
                                    ]
                                }
                                for index in range(12)
                            ]
                        },
                    },
                )
                deadline = time.monotonic() + 30
                while not Dataset.objects.get(pk=dataset["id"]).active_cell:
                    assert time.monotonic() < deadline
                    time.sleep(0.1)
                started = time.monotonic()
                prep = mcp.call(
                    "prepare_training_data",
                    {
                        "dataset": dataset["id"],
                        "base_model": "Qwen/Qwen3-1.7B",
                        "context_length": 4096,
                    },
                )
                deadline = started + 60
                while TrainingPreparation.objects.get(pk=prep["id"]).state not in (
                    "ready",
                    "failed",
                ):
                    assert time.monotonic() < deadline, "training preparation stalled"
                    fake_modal.release("prepare_")
                    reconcile_preparation.delay()
                    time.sleep(1)
                saved = TrainingPreparation.objects.get(pk=prep["id"])
                assert saved.state == "ready", saved.error
                preparation_times.append(time.monotonic() - started)
                job = mcp.call(
                    "start_finetune",
                    {
                        "dataset": dataset["id"],
                        "base_model": "Qwen/Qwen3-1.7B",
                        "validation_enabled": False,
                        "eval_model_before": False,
                        "eval_model_after": False,
                        "eval_incumbent_before": False,
                        "eval_incumbent_after": False,
                        "request_key": "mixed-training-" + dataset["id"],
                    },
                )["finetune"]["id"]
                active = provider_finished = None
                next_tick = 0
                deadline = time.monotonic() + 120
                while FinetuningJob.objects.get(pk=job).status not in (
                    "succeeded",
                    "deploying",
                    "failed",
                    "cancelled",
                ):
                    assert time.monotonic() < deadline, "training lifecycle stalled"
                    fake_modal.release("prepare_")
                    if fake_modal.pending("sft_"):
                        active = active or time.monotonic()
                        if time.monotonic() - active >= 10:
                            fake_modal.release("sft_")
                            provider_finished = time.monotonic()
                    if time.monotonic() >= next_tick:
                        reconcile_preparation.delay()
                        reconcile_finetuning_jobs.delay()
                        next_tick = time.monotonic() + 15
                    time.sleep(0.2)
                result = FinetuningJob.objects.get(pk=job)
                assert result.status in ("deploying", "succeeded"), result.error_message
                assert active is not None and provider_finished is not None
                training_times.append(time.monotonic() - active)
                overlap["training"].append((active, provider_finished))
        except Exception as exc:
            errors.append(repr(exc))
        finally:
            close_old_connections()

    ledger = TaskLedger()
    with ExitStack() as stack:
        before_task_publish.connect(ledger.on_publish)
        task_postrun.connect(ledger.on_finish)
        stack.callback(before_task_publish.disconnect, ledger.on_publish)
        stack.callback(task_postrun.disconnect, ledger.on_finish)
        interactive = stack.enter_context(ProcessWorker(settings, settings.MEDIA_ROOT.parent))
        stack.enter_context(
            ProcessWorker(
                settings, settings.MEDIA_ROOT.parent, queue="landing", concurrency=landing_slots
            )
        )
        for queue, concurrency in (
            ("control", 2),
            ("io", 4),
            ("io_traces", 4),
            ("batch", 2),
        ):
            stack.enter_context(
                start_worker(
                    app,
                    pool="threads",
                    concurrency=concurrency,
                    queues=[queue],
                    hostname=f"mixed-{queue}-{uuid.uuid4().hex}",
                    perform_ping_check=False,
                    use_eventloop=False,
                    shutdown_timeout=120,
                )
            )
        # No GPU/provider model calls are needed for retained identity pipelines.
        pipeline_runner.heartbeat()
        recipes = {}
        for index in (0, 1):
            project, user, key = identities[index]
            recipes[index] = McpSurface(live_api.url, key).call(
                "save_dataset_pipeline",
                {
                    "name": "Capacity identity",
                    "request_key": "capacity",
                    "package": retained_package(project, user, [identity_script()]),
                },
            )["pipeline"]
        quiet_dataset, _ = upload(0, 4, 16)
        report["baseline_reads"] = percentiles([read(0) for _ in range(40)])
        report["baseline_pipeline_seconds"] = pipeline(0, quiet_dataset, recipes[0])
        samples, quiet_jobs = [], []
        resource_samples = []
        started = time.monotonic()
        try:
            with ThreadPoolExecutor(max_workers=clients + 4) as executor:
                futures = [executor.submit(reader, i) for i in range(clients)]
                futures.append(executor.submit(writer))
                futures.append(executor.submit(agent_work))
                futures.append(executor.submit(inference_work))
                futures.append(executor.submit(preparation_work))
                try:
                    while time.monotonic() - started < seconds:
                        if errors:
                            break
                        began = time.monotonic()
                        samples.append(read(0))
                        overlap["quiet_reads"].append((began, time.monotonic()))
                        if len(samples) % 100 == 0:
                            with connection.cursor() as cursor:
                                cursor.execute(
                                    "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"
                                )
                                connections = cursor.fetchone()[0]
                            resource_samples.append(
                                {
                                    "elapsed_seconds": time.monotonic() - started,
                                    "database_connections": connections,
                                    "interactive_worker": interactive.stats(),
                                    "max_rss_bytes": resource.getrusage(
                                        resource.RUSAGE_SELF
                                    ).ru_maxrss
                                    * (1 if sys.platform == "darwin" else 1024),
                                }
                            )
                        if len(samples) % 20 == 0:
                            quiet_jobs.append(pipeline(0, quiet_dataset, recipes[0]))
                        time.sleep(0.02)
                finally:
                    stop.set()
                for future in futures:
                    future.result(timeout=180)
            report.update(
                elapsed_seconds=time.monotonic() - started,
                quiet_reads=percentiles(samples),
                competing_requests=sum(counts),
                quiet_pipeline_seconds=quiet_jobs,
                pipeline_receipts=pipeline_receipts,
                large_pipeline_seconds=pipeline_times,
                uploaded_bytes=sum(large_bytes),
                completed_large_uploads=len(large_bytes),
                errors=errors,
                evaluation_seconds=eval_times,
                trace_batch_seconds=trace_times,
                inference_seconds=inference_times,
                resource_samples=resource_samples,
                training_preparation_seconds=preparation_times,
                training_lifecycle_seconds=training_times,
                concurrent_windows={
                    name: [[a - started, b - started] for a, b in windows]
                    for name, windows in overlap.items()
                },
                external_training="simulated Modal GPU; real platform preparation, dispatch, monitoring and provider completion; subsequent serving startup excluded",
                oversized_query_rejection_seconds=oversized_queries,
            )
            assert not errors, errors
            assert (
                large_bytes
                and pipeline_times
                and quiet_jobs
                and eval_times
                and trace_times
                and inference_times
                and preparation_times
                and training_times
            ), report
            simultaneous = []
            neighbours = ("imports", "pipelines", "traces", "inference", "quiet_reads")
            for a, b in overlap["training"]:
                candidates = {a} | {
                    c for name in neighbours for c, _ in overlap[name] if a <= c < b
                }
                simultaneous.extend(
                    point - started
                    for point in sorted(candidates)
                    if all(any(c <= point < d for c, d in overlap[name]) for name in neighbours)
                )
            report["all_workloads_overlap_at_seconds"] = simultaneous
            assert simultaneous, "No shared window with training and every competing workload"
            assert report["quiet_reads"]["p95"] <= max(0.5, 2 * report["baseline_reads"]["p95"])
            assert report["quiet_reads"]["p99"] < 1
            assert max(quiet_jobs) < 10, report
        finally:
            path = Path(os.environ.get("OVERMIND_SOAK_REPORT", "/tmp/overmind-mixed-soak.json"))
            path.write_text(json.dumps(report, indent=2))
            print(f"MIXED_SOAK_REPORT={path}")
            drain_process_results(ledger, timeout=120)
            assert not ledger.failed(), ledger.failed()
