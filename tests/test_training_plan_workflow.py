import json
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

from overbae.models import (
    APIToken,
    Dataset,
    FinetuningJob,
    Project,
    ProjectMembership,
    TrainingCheckpoint,
    TrainingExperiment,
    TrainingValidationRun,
    User,
)
from overbae.services import training_experiments
from overbae.services.datasets import land
from overbae.services.datasets.partition_plans import build
from overbae.services.mcp.server import create_mcp_application

pytestmark = pytest.mark.django_db(transaction=True)


def invoke(client, key, name, arguments, *, error=False):
    response = client.post(
        "/api/mcp/",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"X-Api-Key": key, "Accept": "application/json"},
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert bool(result.get("isError")) == error, result
    body = result.get("structuredContent") or json.loads(result["content"][0]["text"])
    assert json.loads(result["content"][0]["text"]) == body
    return body


@pytest.mark.parametrize("mode", ["decision", "generative"])
def test_mcp_training_plan_keeps_task_data_and_selection_bound(mode, settings):
    settings.STRIPE_SECRET_KEY = ""
    project = Project.objects.create(name="Evidence-bound training")
    user = User.objects.create_user(email=f"{mode}@example.com")
    ProjectMembership.objects.create(project=project, user=user)
    key, _ = APIToken.create_for_user(user, project=project, permission=["read", "write"])
    dataset = Dataset.objects.create(project=project, name="Cases", intent="explore")
    records = []
    for index in range(40):
        row = {"case_id": str(index), "language": "en"}
        if mode == "decision":
            row["decision"] = {
                "state": f"case {index}",
                "question": "Choose",
                "kind": "choice",
                "options": ["yes", "no"],
                "target_probabilities": [1, 0],
                "target_semantics": "categorical_gold",
            }
        else:
            row["messages"] = [
                {"role": "user", "content": f"case {index}"},
                {"role": "assistant", "content": "yes"},
            ]
        records.append(row)
    land.land_rows(dataset, records)
    dataset.refresh_from_db()
    source = str(dataset.active_cell.pk)
    task = {
        "mode": mode,
        "objective": "Predict the supplied case label",
        "prediction_unit": "case",
        "target_meaning": "Synthetic fixture label, not business truth",
        "allowed_inputs": ["case text"],
        "availability_evidence": "The case text exists before prediction",
        "group_by": ["case_id"],
        "slices": ["language"],
        "primary_metric": "/hard_label_accuracy" if mode == "decision" else "/generation/accuracy",
        "success_criterion": "Beat the majority baseline on development; confirm once on final",
    }
    arguments = {
        "name": "Case training",
        "request_key": "task-discovery",
        "source_cell": source,
        "task": task,
    }
    with TestClient(create_mcp_application()) as client:
        discovery = invoke(client, key, "save_training_plan", arguments)["workflow"]["id"]
        assert invoke(client, key, "save_training_plan", arguments)["workflow"]["id"] == discovery
        invoke(
            client,
            key,
            "save_training_plan",
            {**arguments, "task": {**task, "objective": "Changed task"}},
            error=True,
        )
        with patch("overbae.tasks.data_partitions.build_plan.delay"):
            partition = invoke(
                client,
                key,
                "create_data_partition",
                {
                    "name": "Case split",
                    "request_key": "case-split",
                    "source_cell": source,
                    "recipe": {
                        "seed": 37,
                        "group_by": ["case_id"],
                        "fractions": {"train": 0.7, "development": 0.2, "final": 0.1},
                    },
                },
            )["workflow"]["id"]
        build(partition)
        prepared = invoke(
            client,
            key,
            "save_training_plan",
            {
                **arguments,
                "request_key": "task-prepared",
                "supersedes": discovery,
                "prepared_cell": source,
                "partition": partition,
            },
        )["workflow"]["id"]
        details = invoke(client, key, "get_job", {"kind": "training_plan", "id": prepared})[
            "progress"
        ]
        assert details["state"] == "needs_evidence"
        assert details["bindings"]["source"]["fingerprint"] == dataset.active_cell.fingerprint
        assert details["task"] == task
        assert "baseline" in details["missing_evidence"]
        train = details["bindings"]["roles"]["train"]["cell"]
        development = details["bindings"]["roles"]["development"]["cell"]
        variant = {
            "name": "Bounded pilot",
            "base_model": "Qwen/Qwen3.5-4B",
            "cell": train,
            "development_cell": development,
            "hyperparameters": {
                "n_epochs": 1,
                "max_steps": 32,
                "monitoring": {"selection": "development_loss", "initial": True, "final": True},
            },
        }
        experiment = {
            "name": "Pilot",
            "purpose": "Test learnability",
            "request_key": "pilot",
            "training_plan": prepared,
            "stage": "pilot",
            "variants": [variant],
        }
        invoke(
            client, key, "create_training_experiment", {**experiment, "stage": "full"}, error=True
        )
        evidence = [
            {
                "kind": kind,
                "summary": "Controlled fixture measurement",
                "method": "Retained deterministic fixture",
                "cells": [train, development],
                "limitations": ["Synthetic fixture, not business evidence"],
            }
            for kind in ["audit", "baseline", "sanity_check"]
        ]
        ready = invoke(
            client,
            key,
            "save_training_plan",
            {
                **arguments,
                "request_key": "task-ready-without-pilot",
                "supersedes": prepared,
                "prepared_cell": source,
                "partition": partition,
                "evidence": evidence,
            },
        )["workflow"]["id"]
        ready_details = invoke(client, key, "get_job", {"kind": "training_plan", "id": ready})[
            "progress"
        ]
        assert ready_details["state"] == "ready"
        assert ready_details["missing_evidence"] == []
        assert ready_details["selection"] is None
        assert not FinetuningJob.objects.filter(project=project).exists()
        full_hp = {k: v for k, v in variant["hyperparameters"].items() if k != "max_steps"}
        direct_full = invoke(
            client,
            key,
            "create_training_experiment",
            {
                **experiment,
                "name": "Full run without a pilot",
                "request_key": "full-without-pilot",
                "training_plan": ready,
                "stage": "full",
                "variants": [{**variant, "hyperparameters": full_hp}],
            },
        )["workflow"]["id"]
        training_experiments.prepare(TrainingExperiment.objects.get(pk=direct_full))
        with patch("overbae.tasks.finetuning.run_finetuning.apply_async") as dispatch:
            dispatch.return_value.id = "direct-full-fixture-dispatch"
            invoke(client, key, "launch_training_experiment", {"experiment": direct_full})
            invoke(client, key, "launch_training_experiment", {"experiment": direct_full})
        assert dispatch.call_count == 1
        direct_job = FinetuningJob.objects.get(group_id=direct_full)
        assert direct_job.requested_configuration["training_plan"]["selection"] is None
        assert direct_job.requested_configuration["training_plan"]["stage"] == "full"
        FinetuningJob.objects.filter(pk=direct_job.pk).update(status="succeeded")
        pilot = invoke(client, key, "create_training_experiment", experiment)["workflow"]["id"]
        saved = TrainingExperiment.objects.get(pk=pilot)
        training_experiments.prepare(saved)
        with patch("overbae.tasks.finetuning.run_finetuning.apply_async") as dispatch:
            dispatch.return_value.id = "fixture-dispatch"
            invoke(client, key, "launch_training_experiment", {"experiment": pilot})
        job = FinetuningJob.objects.get(group_id=pilot)
        FinetuningJob.objects.filter(pk=job.pk).update(status="succeeded")
        check = TrainingValidationRun.objects.create(
            job=job,
            key="1:32",
            attempt=1,
            stream="development",
            step=32,
            state="completed",
            policy_fingerprint="a" * 64,
            sample_fingerprint="b" * 64,
            receipt_fingerprint="c" * 64,
            metrics={
                "eval_loss": 0.2,
                "hard_label_accuracy": 0.9,
                "generation": {"accuracy": 0.9, "expected": 8, "scored": 8},
            },
            coverage={"expected": 8, "scored": 8},
        )
        TrainingCheckpoint.objects.create(
            job=job,
            key="1:32",
            attempt=1,
            step=32,
            state="available",
            identity="d" * 64,
            verification={"reload_verified": True},
            selected=True,
        )
        selected_args = {
            **arguments,
            "request_key": "task-selected",
            "supersedes": prepared,
            "prepared_cell": source,
            "partition": partition,
            "evidence": evidence,
            "selection": {
                "check": str(check.pk),
                "rationale": "Improved development loss; same recipe, longer training",
                "budget_fields": ["max_steps"],
            },
        }
        selected = invoke(client, key, "save_training_plan", selected_args)["workflow"]["id"]
        snapshot = invoke(client, key, "get_job", {"kind": "training_plan", "id": selected})[
            "progress"
        ]
        assert snapshot["state"] == "selected"
        assert snapshot["selection"]["measurement"]["metrics"]["eval_loss"] == 0.2
        assert snapshot["selection"]["measurement"]["job"] == str(job.pk)
        full = {
            **experiment,
            "request_key": "full",
            "training_plan": selected,
            "stage": "full",
            "variants": [
                {**variant, "hyperparameters": {**variant["hyperparameters"], "max_steps": 64}}
            ],
        }
        accepted = invoke(client, key, "create_training_experiment", full)["workflow"]["id"]
        assert invoke(client, key, "create_training_experiment", full)["workflow"]["id"] == accepted
        training_experiments.prepare(TrainingExperiment.objects.get(pk=accepted))
        with patch("overbae.tasks.finetuning.run_finetuning.apply_async") as dispatch:
            dispatch.return_value.id = "full-fixture-dispatch"
            invoke(client, key, "launch_training_experiment", {"experiment": accepted})
            invoke(client, key, "launch_training_experiment", {"experiment": accepted})
        assert dispatch.call_count == 1
        full_job = FinetuningJob.objects.get(group_id=accepted)
        assert full_job.requested_configuration["training_plan"]["id"] == selected
        assert full_job.requested_configuration["training_plan"]["selection"]["measurement"][
            "job"
        ] == str(job.pk)
        invoke(
            client,
            key,
            "create_training_experiment",
            {
                **full,
                "request_key": "changed-lr",
                "variants": [
                    {
                        **full["variants"][0],
                        "hyperparameters": {
                            **full["variants"][0]["hyperparameters"],
                            "learning_rate": 0.1,
                        },
                    }
                ],
            },
            error=True,
        )
        invoke(
            client,
            key,
            "save_training_plan",
            {
                **selected_args,
                "request_key": "stale-task",
                "task": {**task, "objective": "Different target"},
            },
            error=True,
        )
        invoke(
            client,
            key,
            "create_training_experiment",
            {
                **experiment,
                "request_key": "final-leak",
                "variants": [
                    {**variant, "development_cell": details["bindings"]["roles"]["final"]["cell"]}
                ],
            },
            error=True,
        )
        invoke(
            client,
            key,
            "save_training_plan",
            {
                **arguments,
                "request_key": "fabricated-pilot",
                "supersedes": prepared,
                "prepared_cell": source,
                "partition": partition,
                "selection": {
                    "check": "00000000-0000-0000-0000-000000000000",
                    "rationale": "Claimed success",
                    "budget_fields": ["max_steps"],
                },
            },
            error=True,
        )
        original = invoke(client, key, "get_job", {"kind": "training_plan", "id": discovery})[
            "progress"
        ]
        assert original["bindings"].get("partition") is None
        stranger = Project.objects.create(name="Foreign", slug="foreign")
        ProjectMembership.objects.create(project=stranger, user=user)
        foreign_key, _ = APIToken.create_for_user(
            user, project=stranger, permission=["read", "write"]
        )
        invoke(
            client, foreign_key, "get_job", {"kind": "training_plan", "id": prepared}, error=True
        )
