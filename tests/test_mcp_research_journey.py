import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.utils import timezone
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_decision_provider_recovery import fixture, payload

from overbae.models import (
    APIToken,
    Dataset,
    NativeEvaluationPlan,
    Project,
    TrainingExperiment,
    User,
)
from overbae.services import native_evaluation, training_experiments
from overbae.services.datasets import exploration, land, paths, rows, sampling, store
from overbae.services.datasets.examples import native_decision
from overbae.services.datasets.exploration import advance
from overbae.services.decision_providers import external_step
from overbae.services.mcp.server import create_mcp_application
from overbae.tasks.native_evaluation import advance_plan

pytestmark = pytest.mark.django_db(transaction=True)


def call(client, key, name, arguments):
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
    assert not result.get("isError"), result
    return result.get("structuredContent") or json.loads(result["content"][0]["text"])


@pytest.mark.parametrize("tool", ["derive_dataset", "explore_dataset"])
def test_exploration_request_recovery_survives_profiler_upgrade(tool):
    project, _, dataset = workspace()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    arguments = {
        "name": "Frozen request",
        "source_cell": str(dataset.active_cell.pk),
        "request_key": "recover-after-profiler-upgrade",
    }
    with (
        TestClient(create_mcp_application()) as client,
        patch("overbae.tasks.data_exploration.run.delay"),
    ):
        with patch.object(exploration, "PROFILE_VERSION", 1):
            original = call(client, key, tool, arguments)
        advance(original["workflow"]["id"])
        with patch.object(exploration, "PROFILE_VERSION", 2):
            recovered = call(client, key, tool, arguments)
            assert recovered["workflow"]["id"] == original["workflow"]["id"]
            fresh = call(
                client, key, tool, {**arguments, "request_key": "new-profiler-measurement"}
            )
        advance(fresh["workflow"]["id"])
        measured = call(
            client, key, "get_job", {"kind": "data_exploration", "id": fresh["workflow"]["id"]}
        )
        assert measured["status"] == "completed"
        if tool == "explore_dataset":
            assert "reused_from" not in measured["progress"]["report"]


def test_training_draft_validation_names_the_recipe_correction_without_log_access(settings):
    settings.FINETUNING_BACKEND = "modal"
    project, _, dataset = workspace()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    arguments = {
        "name": "Foundation feasibility",
        "purpose": "Get a forecast without launching training",
        "request_key": "corrected-recipe",
        "variants": [
            {
                "name": "Foundation",
                "cell": str(dataset.active_cell.pk),
                "base_model": "Qwen/Qwen3.5-4B",
                "hyperparameters": {"n_epochs": 1, "training_type": "lora"},
            }
        ],
    }
    with TestClient(create_mcp_application()) as client:
        refused = client.post(
            "/api/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "create_training_experiment", "arguments": arguments},
            },
            headers={"X-Api-Key": key, "Accept": "application/json"},
        ).json()["result"]
        assert refused["isError"]
        error = refused["structuredContent"]["error"]
        assert error["code"] == "invalid_input"
        assert "training_type must be an object" in error["fields"]["variants.0.hyperparameters"]
        assert '{"type": "Lora"}' in error["fields"]["variants.0.hyperparameters"]
        arguments["variants"][0]["hyperparameters"]["training_type"] = {"type": "Lora"}
        draft = call(client, key, "create_training_experiment", arguments)
        job = call(
            client, key, "get_job", {"kind": "training_experiment", "id": draft["workflow"]["id"]}
        )
        assert job["status"] == "draft"
        assert job["progress"]["jobs"] == []


def test_unmeasured_forecast_explains_budget_stop_before_a_launch_attempt(settings):
    settings.FINETUNING_BACKEND = "modal"
    project, _, dataset = workspace()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    with TestClient(create_mcp_application()) as client:
        draft = call(
            client,
            key,
            "create_training_experiment",
            {
                "name": "Bounded budget",
                "purpose": "Do not launch an unmeasured training recipe",
                "request_key": "budgeted-draft",
                "constraints": {"max_training_run_usd": 70},
                "variants": [
                    {
                        "name": "Foundation",
                        "cell": str(dataset.active_cell.pk),
                        "base_model": "Qwen/Qwen3.5-4B",
                        "hyperparameters": {"n_epochs": 1},
                    }
                ],
            },
        )
        experiment = TrainingExperiment.objects.get(pk=draft["workflow"]["id"])
        training_experiments.prepare(experiment)
        status = call(
            client, key, "get_job", {"kind": "training_experiment", "id": str(experiment.pk)}
        )["progress"]
        assert status["state"] == "prepared"
        assert status["jobs"] == []
        assert "launch_training_experiment" not in status["next_actions"]
        assert status["launch_readiness"]["allowed"] is False
        assert status["launch_readiness"]["reason"]
        assert status["launch_readiness"]["constraints"]["max_training_run_usd"] == 70
        quote = status["protocol"]["forecast"]["variants"][0]
        assert quote["trained_tokens"] >= 0


def test_fresh_mcp_client_derives_and_profiles_exact_parent_without_changing_it():

    project, _, dataset = workspace()
    user = project.memberships.first().user
    key, _ = APIToken.create_for_user(user, project=project, permission=["read", "write"])
    original = dataset.active_cell
    fingerprint = original.fingerprint
    with (
        TestClient(create_mcp_application()) as client,
        patch("overbae.tasks.data_exploration.run.delay"),
    ):
        result = call(
            client,
            key,
            "derive_dataset",
            {
                "source_cell": str(original.pk),
                "name": "Audit source",
                "request_key": "derive-audit",
            },
        )
        operation = result["workflow"]["id"]
        advance(operation)
        status = call(client, key, "get_job", {"kind": "data_exploration", "id": operation})
        assert status["status"] == "completed"
        derived = Dataset.objects.get(pk=status["progress"]["output_dataset"])
        assert derived.id != dataset.id
        assert [r.extra["decision"] for r in rows.iter_rows(derived.active_cell)] == [
            r.extra["decision"] for r in rows.iter_rows(original)
        ]
        repeated = call(
            client,
            key,
            "derive_dataset",
            {
                "source_cell": str(original.pk),
                "name": "Audit source",
                "request_key": "derive-audit",
            },
        )
        assert repeated["workflow"]["id"] == operation
        dataset.refresh_from_db()
        assert dataset.active_cell.pk == original.pk
        assert dataset.active_cell.fingerprint == fingerprint
        profile = call(
            client,
            key,
            "explore_dataset",
            {
                "source_cell": str(original.pk),
                "name": "Audit coverage",
                "request_key": "coverage",
                "sampling": {"rows": 1, "seed": 37, "stratify_by": ["family"], "target_type": True},
            },
        )
        advance(profile["workflow"]["id"])
        report = call(
            client, key, "get_job", {"kind": "data_exploration", "id": profile["workflow"]["id"]}
        )["progress"]["report"]
        assert report["sampling"]["feasible"] is False
        assert report["sampling"]["minimum_rows"] == 2
        assert report["source"]["fingerprint"] == fingerprint


def test_target_sampling_preserves_mean_and_probability_supervision():

    project, _, _ = workspace()
    dataset = Dataset.objects.create(project=project, name="Mixed ratings", intent="train")
    examples = [
        {
            "decision": {
                "state": "Example",
                "question": "Rate",
                "kind": "score",
                "options": ["1", "2", "3"],
                "target_mean": 2.2,
                "option_values": [1, 2, 3],
                "target_semantics": "ordinal_mean",
            }
        },
        {
            "decision": {
                "state": "Example",
                "question": "Choose",
                "kind": "choice",
                "options": ["a", "b"],
                "target_probabilities": [0.2, 0.8],
            }
        },
    ]
    land.land_rows(dataset, examples)
    dataset.refresh_from_db()

    def frames():
        return store.iter_frames(rows.frame_path(dataset.active_cell))

    selected = [
        row
        for frame in sampling.sample_frames(
            frames, native_decision, rows=2, seed=1, target_type=True
        )
        for row in frame.to_dict("records")
    ]
    assert [row["decision"] for row in selected] == [row["decision"] for row in examples]


def test_mcp_derives_historical_chat_source_with_tool_messages_and_lineage():
    project, _, _ = workspace()
    dataset = Dataset.objects.create(project=project, name="Support conversations", intent="train")
    messages = [
        {"role": "user", "content": "Find the delivery"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "lookup",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": '{"order":"123"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "lookup", "content": "Arrives Monday"},
        {"role": "assistant", "content": "Your order arrives Monday."},
    ]
    land.land_rows(dataset, [{"messages": messages, "group_id": "same-session"}] * 2)
    dataset.refresh_from_db()
    original = dataset.active_cell
    from conftest import import_version

    latest = import_version(
        dataset,
        store.head(paths.cell_path(dataset.pk, dataset.source.pk), 1),
        name="One conversation",
    )
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == latest.pk
    assert dataset.active_cell.rows == 1
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    with (
        TestClient(create_mcp_application()) as client,
        patch("overbae.tasks.data_exploration.run.delay"),
    ):
        receipt = call(
            client,
            key,
            "derive_dataset",
            {
                "source_cell": str(original.pk),
                "name": "Full conversations",
                "request_key": "chat-parent",
            },
        )
        advance(receipt["workflow"]["id"])
        status = call(
            client, key, "get_job", {"kind": "data_exploration", "id": receipt["workflow"]["id"]}
        )
    assert status["status"] == "completed"
    derived = Dataset.objects.get(pk=status["progress"]["output_dataset"])
    records = list(store.iter_rows(rows.frame_path(derived.active_cell)))
    assert len(records) == 2
    assert all(record["messages"] == messages for record in records)
    assert all(record["group_id"] == "same-session" for record in records)
    assert [record["_overmind_provenance"]["derived_from"]["row"] for record in records] == [0, 1]
    assert all(
        record["_overmind_provenance"]["derived_from"]["cell"] == str(original.pk)
        for record in records
    )
    dataset.refresh_from_db()
    assert dataset.active_cell.pk == latest.pk


def test_mcp_comparison_draft_requires_explicit_launch_and_resumes_after_reconnect():

    project, _, dataset = workspace()
    dataset.intent = "eval"
    dataset.save()
    key, _ = APIToken.create_for_user(
        project.memberships.first().user, project=project, permission=["read", "write"]
    )
    with (
        TestClient(create_mcp_application()) as client,
        patch("overbae.tasks.native_evaluation.advance_plan.delay") as dispatch,
    ):
        result = call(
            client,
            key,
            "create_native_evaluation",
            {
                "name": "Saved comparison",
                "request_key": "draft-comparison",
                "final_cell": str(dataset.active_cell.pk),
                "participants": [
                    {
                        "key": "external",
                        "name": "External",
                        "kind": "external",
                        "model": "typesafe/jev-1.13",
                    }
                ],
                "baseline": "external",
            },
        )
        identifier = result["workflow"]["id"]
        assert result["workflow"]["state"] == "draft"
        dispatch.assert_not_called()
    with (
        TestClient(create_mcp_application()) as client,
        patch("overbae.tasks.native_evaluation.advance_plan.delay") as dispatch,
    ):
        result = call(client, key, "launch_native_evaluation", {"evaluation": identifier})
        repeated = call(client, key, "launch_native_evaluation", {"evaluation": identifier})
        assert repeated["workflow"]["id"] == identifier
        assert NativeEvaluationPlan.objects.filter(pk=identifier).count() == 1
        assert result["workflow"]["state"] == "queued"
        assert dispatch.call_count == 1


def test_independent_comparison_stages_survive_one_failed_arm_and_pause():

    project, _, dataset = workspace()
    dataset.intent = "eval"
    dataset.save()
    plan = native_evaluation.create_plan(
        project,
        name="Independent arms",
        request_key="independent",
        final_cell=dataset.active_cell,
        baseline="first",
        participants=[
            {"key": key, "name": key, "kind": "external", "model": "typesafe/jev-1.13"}
            for key in ("first", "second")
        ],
    )
    with patch("overbae.tasks.native_evaluation.advance_plan.delay"):
        plan = native_evaluation.launch(plan)
    native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    assert native_evaluation.ready_stages(plan) == ["final_first", "final_second"]
    with patch(
        "overbae.services.native_evaluation.external_step",
        side_effect=ValueError("invalid stored response"),
    ):
        native_evaluation.advance(plan.pk, stage="final_first")
    plan.refresh_from_db()
    assert native_evaluation.ready_stages(plan) == ["final_second"]
    native_evaluation.pause(plan)
    plan.refresh_from_db()
    assert native_evaluation.ready_stages(plan) == []
    with patch("overbae.services.native_evaluation.external_step") as submit:
        native_evaluation.advance(plan.pk, stage="final_second")
        submit.assert_not_called()


def test_saved_prediction_reuse_is_explicit_and_refuses_changed_conditions():

    source, participant, _ = fixture()
    with patch(
        "overbae.core.decisions.request_once", return_value=(payload(), {"response_cost": 0.002})
    ):
        receipt = external_step(source, "final", participant)
    source.calls = {"final_external": {"state": "completed", "receipt": receipt}}
    source.save()
    target = native_evaluation.create_plan(
        source.project,
        name="Reuse",
        request_key="reuse",
        final_cell=source.final_cell,
        participants=[participant],
        baseline="external",
    )
    native_evaluation.reuse_predictions(
        target, source=source, participant="external", source_participant="external"
    )
    target.refresh_from_db()
    with patch("overbae.tasks.native_evaluation.advance_plan.delay"):
        target = native_evaluation.launch(target)
    with patch(
        "overbae.core.decisions.request_once",
        side_effect=AssertionError("never repeat cached work"),
    ):
        native_evaluation.advance(target.pk, stage="verify_inputs")
        native_evaluation.advance(target.pk, stage="final_external")
    target.refresh_from_db()
    assert target.calls["final_external"]["state"] == "completed"
    assert target.calls["final_external"]["receipt"]["reused_from"] == str(source.pk)
    assert not target.requests.exists()

    incompatible = native_evaluation.create_plan(
        source.project,
        name="Different inference",
        request_key="incompatible-reuse",
        final_cell=source.final_cell,
        participants=[participant],
        baseline="external",
        inference={"context_length": 1024},
    )
    with pytest.raises(ValueError, match="identical sources"):
        native_evaluation.reuse_predictions(
            incompatible, source=source, participant="external", source_participant="external"
        )
    incompatible.refresh_from_db()
    assert not incompatible.config.get("reuse")
    foreign = Project.objects.create(name="Other tenant", slug="reuse-other-tenant")
    NativeEvaluationPlan.objects.filter(pk=incompatible.pk).update(project=foreign)
    with pytest.raises(ValueError, match="same project"):
        native_evaluation.reuse_predictions(
            incompatible, source=source, participant="external", source_participant="external"
        )


def test_expired_stage_lease_is_reconciled_after_worker_loss():

    plan, _, _ = fixture()
    with patch("overbae.tasks.native_evaluation.advance_plan.delay"):
        native_evaluation.launch(plan)
    plan.refresh_from_db()
    plan.calls = {
        "verify_inputs": {
            "state": "running",
            "lease": "lost",
            "lease_started_at": (timezone.now() - timedelta(seconds=1300)).isoformat(),
        }
    }
    plan.save()
    with patch.object(advance_plan, "apply_async") as queued:
        advance_plan(str(plan.pk))
    assert queued.call_count >= 1
    native_evaluation.advance(plan.pk, stage="verify_inputs")
    plan.refresh_from_db()
    assert plan.calls["verify_inputs"]["state"] == "completed"


def test_exploration_reports_allocation_and_can_retry_failed_local_work():

    project, _, dataset = workspace()
    op = exploration.request(
        project,
        source_cell=dataset.active_cell,
        name="Coverage",
        request_key="alloc",
        kind="profile",
        sampling_request={"rows": 8, "seed": 1, "stratify_by": ["family"]},
    )
    with patch.object(
        exploration.profile, "profile_records", side_effect=OSError("temporary local failure")
    ):
        exploration.advance(op.pk)
    op.refresh_from_db()
    assert op.state == "failed"
    repeated = exploration.request(
        project,
        source_cell=dataset.active_cell,
        name="Coverage",
        request_key="alloc",
        kind="profile",
        sampling_request={"rows": 8, "seed": 1, "stratify_by": ["family"]},
    )
    assert repeated.state == "queued"
    exploration.advance(op.pk)
    op.refresh_from_db()
    page = exploration.strata(op, limit=1, offset=0)
    second = exploration.strata(op, limit=1, offset=1)
    assert page["total"] == 2
    assert sum(r["allocation"] for p in [page, second] for r in p["strata"]) == 8
    assert op.report["measurement"]["population"] == "whole_source"


def test_account_client_discovers_interface_without_selecting_a_project():

    user = User.objects.create_user(email="interface-only@example.com")
    key, _ = APIToken.create_for_user(user, permission=["read"])
    with TestClient(create_mcp_application()) as client:
        response = client.post(
            "/api/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/read",
                "params": {"uri": "overmind://interface/current"},
            },
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "error" not in body, body
        resource = json.loads(body["result"]["contents"][0]["text"])
        assert resource["contract_version"] == "6.4.0"
        assert len(resource["catalog_sha256"]) == 64
