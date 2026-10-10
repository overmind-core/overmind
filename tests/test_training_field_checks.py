import asyncio
import json
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from modal_shared.training_monitoring import paired_generation_metrics, resolve_policy
from modal_shared.training_monitoring_runtime import Monitor
from overbae.models import APIToken, Dataset, FinetuningJob, Project, ProjectMembership, User
from overbae.services import training_monitoring
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.training_monitoring import TrainingMonitoringPolicy


def policy(fields):
    declared = TrainingMonitoringPolicy.model_validate(
        {"generation": {"kind": "json_fields", "fields": fields, "sample": 10}}
    ).model_dump(mode="json", by_alias=True, exclude_none=True)
    return training_monitoring.resolve(
        {}, has_development=True, provider="modal", monitoring=declared
    )


def score(prediction, reference, fields):
    from modal_shared.training_monitoring import score_json_fields

    return score_json_fields(prediction, reference, fields)


@pytest.mark.parametrize(
    "fields",
    [
        [],
        ["field"],
        ["/bad~2escape"],
        ["/a", "/a"],
        [True],
        ["/" + "a" * 1024],
        [f"/{i}" for i in range(65)],
    ],
)
def test_field_contract_rejects_ambiguous_or_unbounded_paths(fields):
    with pytest.raises(ValueError):
        resolve_policy(
            {"generation": {"kind": "json_fields", "fields": fields}},
            has_development=True,
            provider="modal",
        )


@pytest.mark.parametrize(
    "prediction,reference,fields,passed",
    [
        ('{"value":false}', '{"value":0}', ["/value"], False),
        ('{"value":null}', '{"value":null}', ["/value"], True),
        ("{}", '{"value":null}', ["/value"], False),
        ('{"a/b":{"~key":[1,2]}}', '{"a/b":{"~key":[1,2]}}', ["/a~1b/~0key/1"], True),
        ('{"value":[2,1]}', '{"value":[1,2]}', ["/value"], False),
        ('{"value":9007199254740993}', '{"value":9007199254740992}', ["/value"], False),
        ('{"value":1.0}', '{"value":1}', ["/value"], True),
        ('{"value":1,"extra":"ignored"}', '{"value":1}', ["/value"], True),
        ('{"value":1,"extra":"not ignored"}', '{"value":1}', [""], False),
        ('{"value":0,"value":1}', '{"value":1}', ["/value"], False),
        ('{"value":NaN}', '{"value":null}', ["/value"], False),
        ('{"value":1e400}', '{"value":1}', ["/value"], False),
        ("not JSON", '{"value":1}', ["/value"], False),
    ],
)
def test_field_checks_preserve_json_types_precision_and_declared_scope(
    prediction, reference, fields, passed
):
    measured = score(prediction, reference, fields)
    assert measured["passed"] is passed
    assert measured["scoring_status"] == "completed"
    assert measured["scorer"] == "json_fields:1"


@pytest.mark.parametrize("reference", ["{}", '{"value":NaN}', '{"value":1,"value":2}', "not JSON"])
def test_missing_or_invalid_reference_is_unscorable_not_a_model_failure(reference):
    measured = score('{"value":1}', reference, ["/value"])
    assert measured["passed"] is None
    assert measured["scoring_status"] == "unscorable"
    assert measured["fields"]["/value"]["passed"] is None


@pytest.mark.django_db(transaction=True)
def test_declared_fields_flow_from_policy_through_checks_to_passive_agent_and_console(tmp_path):
    contract = policy(["/risk", "/case/id"])
    rows = [{"key": str(i), "input_ids": [1, 2], "labels": [-100, 2]} for i in range(5)]
    monitor = Monitor(tmp_path, contract, rows, rows, total_steps=10, attempt=1)
    reference = '{"risk":"high","case":{"id":1}}'
    predictions = [reference, '{"risk":"low","case":{"id":1}}', "not JSON", reference, reference]

    def generate(indices):
        return [
            dict(
                row=i,
                key=str(i),
                reference=reference if i != 3 else "{}",
                prediction=predictions[i],
                status="completed" if i != 4 else "failed",
                **score(
                    predictions[i], reference if i != 3 else "{}", contract["generation"]["fields"]
                ),
            )
            for i in indices
        ]

    first = monitor.check(0, evaluate=lambda *_: {"eval_loss": 0.5}, generate=generate)
    assert first["state"] == "completed", first
    metrics = first["metrics"]["generation"]
    assert metrics["expected"] == 5
    assert metrics["scored"] == 3
    assert metrics["technical_errors"] == 1
    assert metrics["unscorable"] == 1
    assert metrics["pass_rate"] == pytest.approx(1 / 3)
    assert metrics["fields"]["/risk"]["pass_rate"] == pytest.approx(1 / 3)
    assert metrics["fields"]["/case/id"]["pass_rate"] == pytest.approx(2 / 3)
    assert metrics["fields"]["/case/id"]["coverage"] == 3 / 5
    second = monitor.check(
        10, final=True, evaluate=lambda *_: {"eval_loss": 0.4}, generate=generate
    )
    assert second["metrics"]["paired_generation"]["paired"] == 3
    assert second["metrics"]["paired_generation"]["unpaired"] == 2

    user = User.objects.create_user(email=f"field-{uuid.uuid4()}@example.com", password="test")
    project = Project.objects.create(name="Field monitoring")
    ProjectMembership.objects.create(user=user, project=project)
    job = FinetuningJob.objects.create(
        project=project,
        dataset=Dataset.objects.create(project=project),
        hyperparameters={"monitoring": contract},
    )
    payload = monitor.summary()
    for check in payload["checks"]:
        check["artifact"].update(json.loads((tmp_path / check["artifact"]["path"]).read_text()))
    training_monitoring.ingest(job, payload)
    context = MCPContext(
        user=user,
        project=project,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
    )
    client = APIClient()
    client.force_authenticate(user)
    with patch("modal.Function.from_name", side_effect=AssertionError("read invoked provider")):
        result = asyncio.run(
            CATALOG.call("inspect_training_progress", {"job": str(job.id)}, context)
        )
        response = client.get(reverse("finetuningjob-monitoring", kwargs={"id": job.id}))
        evidence = asyncio.run(
            CATALOG.call(
                "inspect_training_progress",
                {"job": str(job.id), "check": str(job.validation_runs.order_by("step").first().id)},
                context,
            )
        )
    assert not result.isError, result.structuredContent
    assert response.status_code == 200
    assert result.structuredContent["progress"]["checks"] == response.json()["checks"]
    assert result.structuredContent["progress"]["checks"][0]["metrics"]["generation"] == metrics
    assert not evidence.isError, evidence.structuredContent
    assert evidence.structuredContent["progress"]["items"][3]["passed"] is None


def test_paired_results_exclude_unscorable_reference_instead_of_coercing_null():
    rows = [
        {
            "row": 1,
            "status": "completed",
            "reference": "{}",
            "passed": None,
            "scoring_status": "unscorable",
        }
    ]
    assert paired_generation_metrics(rows, rows, seed=42)["paired"] == 0


@pytest.mark.parametrize("extra", [{"schema": {}}, {"labels": ["yes", "no"]}])
def test_field_contract_does_not_silently_ignore_an_additional_grader(extra):
    with pytest.raises(ValueError, match="json_fields"):
        resolve_policy(
            {"generation": {"kind": "json_fields", "fields": ["/risk"], **extra}},
            has_development=True,
            provider="modal",
        )
