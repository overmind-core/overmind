from types import SimpleNamespace

import pytest
from conftest import frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient

from overbae.core.errors import InputValidationError
from overbae.models import (
    DecisionPerformanceRun,
    DecisionProviderRequest,
    NativeEvaluationPlan,
    Project,
    ProjectMembership,
    TrainingExperiment,
    User,
)
from overbae.services import model_workflows, native_evaluation
from overbae.services.mcp.contracts.model_workflows import ListWorkflowsInput
from overbae.services.mcp.tools_model_workflows import list_workflows
from overbae.tasks.native_evaluation import advance_plan

pytestmark = pytest.mark.django_db


@pytest.fixture
def evaluation():
    user = User.objects.create_user(email="delete-evaluation@example.com")
    project = Project.objects.create(name="Deletion", slug="deletion")
    ProjectMembership.objects.create(project=project, user=user)
    dataset = frozen_dataset(
        project,
        [
            {
                "input": {
                    "decision": {
                        "state": "Evidence",
                        "question": "Choose",
                        "kind": "choice",
                        "options": ["yes", "no"],
                    }
                },
                "expected_output": {"probabilities": [1, 0]},
            }
        ],
        contract="eval",
    )
    plan = NativeEvaluationPlan.objects.create(
        project=project,
        final_cell=dataset.active_cell,
        state="completed",
        request_key="saved-evaluation",
        config={"participants": []},
        results={"recorded": "evidence"},
    )
    client = APIClient()
    client.force_authenticate(user)
    return client, plan


def test_delete_hides_lists_and_retains_references_receipts_and_report(evaluation):
    client, plan = evaluation
    experiment = TrainingExperiment.objects.create(
        project=plan.project,
        evaluation=plan,
        name="Experiment",
        purpose="Compare",
        request_key="experiment",
    )
    performance = DecisionPerformanceRun.objects.create(
        project=plan.project, evaluation=plan, name="Speed", request_key="speed", workload={}
    )
    receipt = DecisionProviderRequest.objects.create(
        plan=plan,
        participant="base",
        role="final",
        input_sha256="a" * 64,
        state="completed",
        usage={"cost": 1},
    )
    report = native_evaluation.directory(plan, "report") / "results.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text('{"recorded": "evidence"}')
    url = reverse("native-evaluation-detail", args=[plan.id])
    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 204
    assert client.get(url).status_code == 404
    plan.refresh_from_db()
    assert plan.deleted_at is not None
    assert plan.results == {"recorded": "evidence"}
    assert TrainingExperiment.objects.get(pk=experiment.pk).evaluation_id == plan.pk
    assert DecisionPerformanceRun.objects.get(pk=performance.pk).evaluation_id == plan.pk
    assert DecisionProviderRequest.objects.get(pk=receipt.pk).usage == {"cost": 1}
    response = client.get(reverse("native-evaluation-report", args=[plan.id]))
    assert response.status_code == 200
    assert b"evidence" in b"".join(response.streaming_content)
    for path in ("/api/native-evaluations/", "/api/eval-runs/activity/"):
        assert client.get(path, {"project": plan.project_id}).data["count"] == 0
    assert client.get("/api/eval-runs/activity-datasets/", {"project": plan.project_id}).data == []
    listing = list_workflows(
        ListWorkflowsInput(kind="native_evaluation"), SimpleNamespace(project=plan.project)
    )
    assert listing.total == 0
    retained = model_workflows.describe("native_evaluation", plan)
    assert retained["deleted_at"]
    assert retained["next_actions"] == ["get_job"]
    assert retained["input_required"] is False


@pytest.mark.parametrize("state", ["draft", "prepared", "paused", "failed", "completed"])
def test_idle_runs_can_be_deleted_and_stale_tasks_do_not_restart_them(evaluation, state):
    client, plan = evaluation
    plan.state = state
    plan.save()
    assert client.delete(reverse("native-evaluation-detail", args=[plan.id])).status_code == 204
    advance_plan(str(plan.id))
    plan.refresh_from_db()
    assert native_evaluation.ready_stages(plan) == []
    assert plan.state == state
    for action in (
        native_evaluation.launch,
        native_evaluation.prepare,
        native_evaluation.resume,
        native_evaluation.pause,
    ):
        with pytest.raises(InputValidationError, match="deleted"):
            action(plan)


@pytest.mark.parametrize(
    "state", ["running", "queued", "preparing", "waiting_for_checkpoint", "submission_unknown"]
)
def test_active_runs_cannot_be_deleted(evaluation, state):
    client, plan = evaluation
    plan.state = state
    plan.save()
    assert client.delete(reverse("native-evaluation-detail", args=[plan.id])).status_code == 400
    plan.refresh_from_db()
    assert plan.deleted_at is None


@pytest.mark.parametrize(
    "evidence", ["lease", "running", "submitting", "submission_unknown", "provider"]
)
def test_pausing_does_not_allow_active_or_unresolved_work_to_be_deleted(evaluation, evidence):
    client, plan = evaluation
    plan.state = "paused"
    if evidence == "provider":
        DecisionProviderRequest.objects.create(
            plan=plan,
            participant="base",
            role="final",
            input_sha256="a" * 64,
            state="submission_unknown",
        )
    else:
        plan.calls = {
            "final_base": {"lease": "worker"} if evidence == "lease" else {"state": evidence}
        }
    plan.save()
    response = client.delete(reverse("native-evaluation-detail", args=[plan.id]))
    assert response.status_code == 400
    assert "active or unresolved" in str(response.data)
    plan.refresh_from_db()
    assert plan.deleted_at is None


def test_deleted_request_key_cannot_create_another_evaluation(evaluation):
    _, plan = evaluation
    native_evaluation.delete(plan)
    with pytest.raises(InputValidationError, match="deleted"):
        native_evaluation.create_plan(
            plan.project,
            name=plan.name,
            request_key=plan.request_key,
            final_cell=plan.final_cell,
            participants=[],
            baseline="base",
        )
    assert NativeEvaluationPlan.objects.filter(request_key=plan.request_key).count() == 1


def test_unfinished_experiment_keeps_its_paused_evaluation(evaluation):
    client, plan = evaluation
    plan.state = "paused"
    plan.save()
    TrainingExperiment.objects.create(
        project=plan.project,
        evaluation=plan,
        name="Training",
        purpose="Compare",
        request_key="training",
        state="evaluating",
    )
    response = client.delete(reverse("native-evaluation-detail", args=[plan.id]))
    assert response.status_code == 400
    assert "training experiment" in str(response.data)
    plan.refresh_from_db()
    assert plan.deleted_at is None


def test_deletion_is_project_scoped(evaluation):
    client, plan = evaluation
    other = Project.objects.create(name="Other", slug="other")
    NativeEvaluationPlan.objects.filter(pk=plan.pk).update(project=other)
    assert client.delete(reverse("native-evaluation-detail", args=[plan.id])).status_code == 404
    plan.refresh_from_db()
    assert plan.deleted_at is None
