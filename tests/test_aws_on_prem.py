from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from fakes.aws import FakeAWS
from pydantic import BaseModel

from overbae.core import decisions, llms, model_registry
from overbae.core.aws import boto3_session
from overbae.models import (
    DedicatedDeployment,
    DeployedModel,
    FinetuningJob,
    Project,
    User,
)
from overbae.sagemaker.instance_map import (
    cuda_visible_devices,
    gpu_from_instance_type,
    sagemaker_instance_type,
)
from overbae.services import (
    dedicated,
    deployment,
    finetuning_checkpoints,
    finetuning_runner,
    inference_requests,
    provider_pricing,
    training_preparation,
    training_release,
    training_submission,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("model-runtime")]


class _VerdictSchema(BaseModel):
    verdict: str
    score: float


@pytest.fixture
def aws_env(settings, monkeypatch):
    monkeypatch.setenv("AWS_BEDROCK_ENABLED", "True")
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("SAGEMAKER_SYSTEMONE_ENDPOINT_NAME", "om-systemone-vpc")
    settings.FINETUNING_BACKEND = "sagemaker"
    settings.AWS_BEDROCK_ENABLED = True
    settings.AWS_REGION = "eu-west-1"
    settings.AWS_BUCKET_NAME = "test-ft-bucket"
    settings.AWS_SFT_BUCKET_NAME = "test-sft-bucket"
    settings.AWS_WEIGHTS_BUCKET_NAME = "test-weights-bucket"
    settings.SAGEMAKER_EXECUTION_ROLE_ARN = "arn:aws:iam::123456789012:role/overmind-sagemaker"
    settings.SAGEMAKER_SYSTEMONE_ENDPOINT_NAME = "om-systemone-vpc"
    fake = FakeAWS()
    with fake.active():
        yield fake


def test_aws_session_and_iam_fallback(monkeypatch, settings):
    settings.AWS_ACCESS_KEY_ID = ""
    settings.AWS_SECRET_ACCESS_KEY = ""
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "")
    session = boto3_session(region_name="eu-west-1")
    assert session.region_name == "eu-west-1"


def test_training_container_modules_do_not_import_django():
    import ast

    root = Path(__file__).resolve().parents[1]
    for rel in (
        "overbae/sagemaker/s3_io.py",
        "overbae/sagemaker/sft_entrypoint.py",
        "overbae/sagemaker/serve_entrypoint.py",
    ):
        tree = ast.parse((root / rel).read_text())
        modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
        assert modules
        assert all(not name.startswith("django") and "s3_store" not in name for name in modules)


def test_ensure_base_model_leaves_the_weights_bucket_empty(aws_env):
    from overbae.sagemaker.deployment_ops import ensure_base_model

    found = ensure_base_model("Qwen/Qwen3-0.6B")
    assert found["cached"] is False
    assert found["base_model_path"].endswith("/.base_models/Qwen--Qwen3-0.6B")
    assert aws_env.s3.buckets["test-weights-bucket"] == {}


def test_instance_map_covers_all_tiers():
    assert sagemaker_instance_type("A10", 1) == "ml.g5.2xlarge"
    assert sagemaker_instance_type("L4", 1) == "ml.g6.2xlarge"
    assert sagemaker_instance_type("L40S", 1) == "ml.g6e.2xlarge"
    assert sagemaker_instance_type("A100-40GB", 1) == "ml.p4d.24xlarge"
    assert sagemaker_instance_type("A100-80GB", 1) == "ml.p4de.24xlarge"
    assert sagemaker_instance_type("H100", 1) == "ml.p5.4xlarge"
    assert sagemaker_instance_type("H100", 4) == "ml.p5.48xlarge"
    assert sagemaker_instance_type("H200", 1) == "ml.p5e.48xlarge"
    assert gpu_from_instance_type("ml.p5.4xlarge") == ("H100", 1)
    assert gpu_from_instance_type("ml.p5e.48xlarge") == ("H200", 8)
    assert cuda_visible_devices("H100", 1) is None
    assert cuda_visible_devices("H200", 1) == "0"
    assert cuda_visible_devices("H100", 4) == "0,1,2,3"


def test_bedrock_llm_completion_tools_schema_and_embeddings(aws_env):
    assert model_registry.bedrock_configured() is True
    resolved = model_registry.resolve_model(model_registry.TaskType.JUDGE_SCORING)
    assert resolved == "claude-sonnet-5"
    assert model_registry.bedrock_model_id(resolved) == "us.anthropic.claude-sonnet-5"

    content, stats = llms.call_llm("Check agent trajectory", system_prompt="Be concise.")
    assert content == "Bedrock VPC completion ok."
    assert stats["prompt_tokens"] == 25
    assert stats["completion_tokens"] == 10
    assert stats["cached_tokens"] == 5
    assert stats["finish_reason"] == "stop"
    assert stats["served_model"] == "us.anthropic.claude-sonnet-5"

    structured, schema_stats = llms.call_llm(
        "Grade the trajectory",
        response_format=_VerdictSchema,
    )
    parsed = json.loads(structured)
    assert parsed == {"verdict": "pass", "score": 1.0}
    assert schema_stats["finish_reason"] == "stop"

    tools = [
        {
            "type": "function",
            "function": {
                "name": "lookup_order",
                "description": "Look up an order",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }
    ]
    text, tool_calls, tool_stats = llms.call_llm_tools(
        [{"role": "user", "content": "Find order 42"}],
        tools,
    )
    assert text == "Calling tool now."
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "lookup_order"
    assert json.loads(tool_calls[0]["function"]["arguments"]) == {"query": "order-42"}
    assert tool_stats["finish_reason"] == "tool_calls"

    vec = llms.get_embedding(" Refund policy text ")
    assert vec == [0.1, 0.2, 0.3, 0.4]
    assert aws_env.bedrock_runtime.invoke_calls[-1]["modelId"] == "amazon.titan-embed-text-v2:0"


def test_sagemaker_systemone_decisions_transport(aws_env, monkeypatch):
    monkeypatch.setattr(decisions, "reserve_capacity", lambda *args, **kwargs: None)
    questions = {
        "q1": decisions.ChoiceQuestion(
            instructions="Did the agent answer accurately?",
            criteria={"yes": "Accurate", "no": "Inaccurate"},
        )
    }
    res = decisions.decide(
        {"input": "hi", "output": "hello"},
        questions,
        project_id="proj-1",
        workload="live_scoring",
        contract="judge@1",
        use_cache=False,
    )
    assert res.answers["q1"].choice == "yes"
    assert res.stats["prompt_tokens"] == 20
    assert aws_env.sagemaker_runtime.invocations[-1]["EndpointName"] == "om-systemone-vpc"

    payload, usage = decisions.request_once(
        {
            "model": "typesafe/jev-1.13",
            "state": "test",
            "questions": {
                "q1": {
                    "type": "choice",
                    "instructions": "Pick one",
                    "criteria": {"yes": "Yes", "no": "No"},
                }
            },
        }
    )
    assert "answers" in payload
    assert usage["prompt_tokens"] == 20


def test_sagemaker_preparation_training_reconcile_and_deployment(aws_env, tmp_path):
    user = User.objects.create_user(email="aws@example.test", password="pw")
    project = Project.objects.create(name="AWS Enterprise", slug="aws-enterprise")
    dataset = frozen_dataset(project, TRAIN_ROWS, contract="train")
    cell = dataset.active_cell

    rel = training_release.current()
    assert rel["backend"] == "sagemaker"
    assert training_release.verify(rel)["sft_bucket"] == "test-sft-bucket"

    prep = training_preparation.begin_preparation(cell, "Qwen/Qwen3-8B", 4096, user=user)
    job = prep.training_jobs.first()
    assert job is not None
    assert job.provider == FinetuningJob.Provider.SAGEMAKER

    # First advance exports rows to S3 and creates SageMaker Processing Job
    training_preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "running"
    assert prep.remote_id.startswith("om-prep-")

    # Second advance polls completed Processing Job and loads report.json from S3
    training_preparation.advance(prep.id)
    prep.refresh_from_db()
    assert prep.state == "ready"
    assert prep.report["ready"] is True

    # Submit training job via SageMakerRunner
    from overbae.services.datasets import rows as row_store
    from overbae.services.finetuning_validator import row_to_finetuning_line

    train_file = tmp_path / "train.jsonl"
    train_file.write_text(
        "\n".join(json.dumps(row_to_finetuning_line(r)) for r in row_store.iter_rows(cell)) + "\n",
        encoding="utf-8",
    )
    job.status = FinetuningJob.Status.PREPARING
    job.requested_configuration = {"runtime": rel}
    job.save(update_fields=["status", "requested_configuration"])

    runner = finetuning_runner.get_runner(job=job)
    assert isinstance(runner, finetuning_runner.SageMakerRunner)

    training_submission.claim(job)
    sub = runner.submit(job, str(train_file), len(TRAIN_ROWS))
    run_id = sub.remote_id.split(":", 1)[0]
    created = aws_env.sagemaker.training_jobs[run_id]
    assert created["ResourceConfig"]["InstanceType"] == "ml.p5.4xlarge"
    assert "CUDA_VISIBLE_DEVICES" not in created["Environment"]
    assert created["kwargs"]["AlgorithmSpecification"]["TrainingImage"].endswith(
        "train-u2026_10_7:latest"
    )
    training_submission.acknowledge(job, sub.remote_id)
    job.remote_job_id = sub.remote_id
    job.status = FinetuningJob.Status.RUNNING
    job.save(update_fields=["remote_job_id", "status"])

    snap = runner.poll(sub.remote_id)
    assert snap.state == "succeeded"
    assert snap.trained_steps == 10
    assert snap.train_loss == 0.42
    assert snap.eval_loss == 0.39
    assert snap.output_model_name.startswith("sagemaker/ft-")

    # Test submission recovery when remote_job_id is missing
    job.remote_job_id = ""
    job.status = FinetuningJob.Status.SUBMISSION_UNKNOWN
    job.provider_submission = {"state": "unknown", "run_id": run_id}
    job.save(update_fields=["remote_job_id", "status", "provider_submission"])
    recovered = training_submission.recover(job)
    assert recovered.status == FinetuningJob.Status.RUNNING
    assert recovered.remote_job_id == sub.remote_id

    # Advance deployment stages (base -> weights -> register -> warm -> ready)
    job.status = FinetuningJob.Status.DEPLOYING
    job.save(update_fields=["status"])
    deployed = deployment.ensure_training_deployment(str(job.id))
    assert deployed is not None

    for _ in range(10):
        deployed.refresh_from_db()
        if deployed.status == DeployedModel.Status.READY and not deployed.deployment_notify:
            break
        deployed.deployment_next_poll_at = None
        deployed.deployment_claim_until = None
        deployed.save(update_fields=["deployment_next_poll_at", "deployment_claim_until"])
        deployment.advance_deployment(deployed.id)

    deployed.refresh_from_db()
    job.refresh_from_db()
    assert deployed.status == DeployedModel.Status.READY
    assert deployed.inference_url.startswith("sagemaker://om-ep-")
    assert job.status == FinetuningJob.Status.SUCCEEDED
    assert (
        aws_env.s3.list_objects_v2(Bucket="test-sft-bucket", Prefix=f"runs/{run_id}/")["KeyCount"]
        == 0
    )

    # Checkpoint download URL works with SAGEMAKER provider and FakeS3
    dl = finetuning_checkpoints.get_checkpoint_download_url(job)
    assert dl is not None
    assert dl["name"] == "checkpoint.zip"
    assert dl["download_url"].startswith("https://s3.fake.aws/test-ft-bucket/")

    # Serverless inference_requests on SageMaker endpoint
    req = inference_requests.submit(
        deployed,
        user,
        "req-sm-1",
        {"messages": [{"role": "user", "content": "Hello"}]},
    )
    inference_requests.advance(req.id)
    req.refresh_from_db()
    if req.state == "running":
        inference_requests.advance(req.id)
        req.refresh_from_db()
    assert req.state == "succeeded"
    assert req.result["content"] == "SageMaker endpoint reply."


def test_failed_sagemaker_run_drops_its_prefix(aws_env):
    from overbae.tasks.finetuning import observe_finetuning_job

    user = User.objects.create_user(email="fail@example.test", password="pw")
    project = Project.objects.create(name="Failed AWS", slug="failed-aws")
    dataset = frozen_dataset(project, TRAIN_ROWS, contract="train")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.SAGEMAKER,
        remote_job_id="ft-fail:arn:aws:sagemaker:eu-west-1:123:training-job/ft-fail",
        hyperparameters={},
        triggered_by=user,
    )
    aws_env.sagemaker.training_jobs["ft-fail"] = {
        "TrainingJobName": "ft-fail",
        "TrainingJobStatus": "Failed",
        "FailureReason": "algorithm exited",
    }
    aws_env.s3.put_object(Bucket="test-sft-bucket", Key="runs/ft-fail/data.jsonl", Body=b"{}\n")
    aws_env.s3.put_object(
        Bucket="test-sft-bucket",
        Key="runs/ft-fail/meta.json",
        Body=b'{"status": "failed", "error": "algorithm exited"}\n',
    )

    observe_finetuning_job(job)

    job.refresh_from_db()
    assert job.status == FinetuningJob.Status.FAILED
    assert (
        aws_env.s3.list_objects_v2(Bucket="test-sft-bucket", Prefix="runs/ft-fail/")["KeyCount"]
        == 0
    )


def test_dedicated_container_fetches_the_checkpoint_zip(aws_env, tmp_path):
    import io
    import zipfile

    from overbae.sagemaker.serve_entrypoint import serve_argv, unzip_checkpoint
    from overbae.sagemaker.serving_runtime import inference_container

    user = User.objects.create_user(email="zip@example.test", password="pw")
    project = Project.objects.create(name="Zip AWS", slug="zip-aws")
    dataset = frozen_dataset(project, TRAIN_ROWS, contract="train")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-0.6B",
        status=FinetuningJob.Status.SUCCEEDED,
        provider=FinetuningJob.Provider.SAGEMAKER,
        remote_job_id="ft-zip:arn",
        triggered_by=user,
        hyperparameters={"training_type": {"type": "Lora"}},
    )
    deployed = DeployedModel.objects.create(
        project=project,
        finetuning_job=job,
        model_id="ft-zip-qwen",
        base_model_id="Qwen/Qwen3-0.6B",
        gpu_type="A10G",
        max_model_len=2048,
        is_lora=True,
        weights_path="/weights/.base_models/unsloth--Qwen3-0.6B",
        status=DeployedModel.Status.READY,
    )
    row = type(
        "Row",
        (),
        {"deployed_model": deployed, "gpu_type": "A10G", "served_model_id": "ft-zip-qwen"},
    )()
    container = inference_container(row)
    assert container["Environment"]["OVERMIND_CHECKPOINT_S3"].endswith(
        f"/{user.id}/{job.id}/checkpoints/checkpoint.zip"
    )
    assert container["Environment"]["OVERMIND_BASE_PREFIX"] == ".base_models/unsloth--Qwen3-0.6B"
    assert "ArtifactUrl" not in container

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr("adapter_config.json", '{"r": 8}')
        archive.writestr("adapter_model.safetensors", b"weights")
    zip_path = tmp_path / "checkpoint.zip"
    zip_path.write_bytes(buf.getvalue())
    out = tmp_path / "checkpoint"
    unzip_checkpoint(zip_path, out)
    argv = serve_argv(
        out,
        out,
        contract="chat",
        served_name="ft-zip-qwen",
        base_model="unsloth/Qwen3-0.6B",
        max_model_len=2048,
        gpu_type="A10G",
    )
    assert f"ft-zip-qwen={out}" in argv
    assert argv[argv.index("--kv-cache-dtype") + 1] == "bfloat16"
    assert "--load-format" not in argv
    full = serve_argv(
        out,
        None,
        contract="chat",
        served_name="ft-full",
        base_model="unsloth/Qwen3-0.6B",
        max_model_len=2048,
        gpu_type="A10G",
    )
    assert "--lora-modules" not in full
    decision = serve_argv(
        out,
        out,
        contract="decision",
        served_name="decision",
        base_model="unsloth/Qwen3-0.6B",
        max_model_len=2048,
        gpu_type="A10G",
    )
    assert "--enable-lora" in decision


def test_sagemaker_dedicated_serving_and_pricing(aws_env):
    rates_card = provider_pricing.refresh_rates()
    assert rates_card["status"] == "current"
    assert rates_card["source"] == "aws.pricing.AmazonSageMaker"
    assert rates_card["rates"]["gpu_hour_cost_h100"] == pytest.approx(6.88)
    assert rates_card["rates"]["gpu_hour_cost_a10g"] == pytest.approx(1.515)

    user = User.objects.create_user(email="ded@example.test", password="pw")
    project = Project.objects.create(name="Dedicated AWS", slug="ded-aws")
    deployed = DeployedModel.objects.create(
        project=project,
        model_id="ft-ded-qwen",
        base_model_id="Qwen/Qwen3-8B",
        gpu_type="H100",
        max_model_len=8192,
        weights_path="/weights/ft-ded-qwen",
        status=DeployedModel.Status.READY,
    )
    ded_row = DedicatedDeployment.objects.create(
        project=project,
        deployed_model=deployed,
        started_by=user,
        gpu_type="H100",
        serving_shape="merged_fp8",
        min_replicas=0,
        max_replicas=4,
        scale_to_zero=True,
        idle_shutdown_seconds=600,
        rate_usd_per_gpu_hour=Decimal("6.88"),
        credits_per_gpu_hour=688,
    )
    dedicated.advance(str(ded_row.id))
    ded_row.refresh_from_db()
    assert ded_row.status == DedicatedDeployment.Status.WARMING
    assert ded_row.probe_call_id.startswith("sm-probe:")
    variant = next(iter(aws_env.sagemaker.endpoint_configs.values()))["ProductionVariants"][0]
    assert variant["ManagedInstanceScaling"]["Status"] == "ENABLED"
    assert variant["ManagedInstanceScaling"]["MinInstanceCount"] == 1
    assert variant["InferenceAmiVersion"] == "al2-ami-sagemaker-inference-gpu-3-1"

    dedicated.advance(str(ded_row.id))
    ded_row.refresh_from_db()
    assert ded_row.status == DedicatedDeployment.Status.ONLINE
    assert aws_env.autoscaling.targets[-1]["MinCapacity"] == 0
    assert aws_env.autoscaling.targets[-1]["MaxCapacity"] == 4

    status_code, raw, content_type = dedicated.serve_chat(
        ded_row,
        json.dumps({"messages": [{"role": "user", "content": "Hello"}]}).encode("utf-8"),
    )
    assert status_code == 200
    assert "application/json" in content_type
    assert json.loads(raw)["choices"][0]["message"]["content"] == "SageMaker endpoint reply."

    chunks = list(
        dedicated.serve_chat_stream(
            ded_row,
            json.dumps({"messages": [{"role": "user", "content": "Stream"}]}).encode("utf-8"),
        )
    )
    assert any(b"Hello from stream" in c for c in chunks)


def test_terraform_and_dockerfile_artifacts_exist():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docker" / "Dockerfile.train").is_file()
    assert (root / "docker" / "Dockerfile.serve").is_file()
    assert (root / ".github" / "workflows" / "deploy-sagemaker-images.yml").is_file()
    for tf_path in (
        root / "infra" / "aws" / "main.tf",
        root / "infra" / "aws" / "variables.tf",
        root / "infra" / "aws" / "outputs.tf",
        root / "infra" / "aws" / "modules" / "networking" / "main.tf",
        root / "infra" / "aws" / "modules" / "storage" / "main.tf",
        root / "infra" / "aws" / "modules" / "ecr" / "main.tf",
        root / "infra" / "aws" / "modules" / "ecs" / "main.tf",
        root / "infra" / "aws" / "modules" / "sagemaker" / "main.tf",
    ):
        assert tf_path.is_file(), f"Missing {tf_path}"
