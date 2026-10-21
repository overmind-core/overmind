"""EvalRun enqueue is patched at Celery apply_async so these tests never spend tokens."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from conftest import EVAL_ROWS, frozen_dataset
from django.conf import settings
from django.test import override_settings
from django.urls import reverse
from factories import auth_client, make_matrix_row

from overbae.innermind import classes, runs
from overbae.models import (
    Capability,
    EvalRun,
    EvalRunRow,
    EvalScore,
    EvalSet,
    FinetuningJob,
    FinetuningJobEval,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import training_release
from overbae.services.finetuning_eval import (
    job_wants_evals,
    serialize_job_evals,
    sync_eval_scores,
    tick_job_evals,
)

pytestmark = pytest.mark.django_db


def _setup(*, with_eval_link=True):
    u = User.objects.create_user(
        email=f"ft-eval-{uuid.uuid4().hex[:8]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    p = Project.objects.create(name="p")
    ProjectMembership.objects.create(user=u, project=p)
    capability = Capability.objects.create(project=p, name="a", slug=f"a-{uuid.uuid4().hex[:6]}")
    train = frozen_dataset(p, EVAL_ROWS, name="train")
    eval_ds = frozen_dataset(p, EVAL_ROWS, name="eval")
    eset = EvalSet.objects.create(project=p, capability=capability, name="set")
    eset.rows.add(make_matrix_row(eset.project))
    job = FinetuningJob.objects.create(
        project=p,
        capability=capability,
        dataset=train,
        eval_dataset=eval_ds if with_eval_link else None,
        eval_set=eset if with_eval_link else None,
        base_model="Qwen/Qwen2.5-0.5B-Instruct",
        eval_incumbent_before=True,
        eval_model_before=False,
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        remote_job_id="proj-1:job-x",
        triggered_by=u,
    )
    return u, p, job, eval_ds, eset


def test_job_wants_evals_requires_both_links():
    _, _, job, _, _ = _setup(with_eval_link=True)
    assert job_wants_evals(job) is True
    job.eval_set = None
    assert job_wants_evals(job) is False


def _scored_run(job, name, row_metrics: list[dict[str, float]], **fields) -> EvalRun:
    """A completed run whose summary is aggregated from real row scores."""
    run = EvalRun.objects.create(
        project=job.project,
        name=name,
        dataset=job.eval_dataset,
        cell=job.eval_dataset.active_cell,
        eval_set=job.eval_set,
        status=EvalRun.Status.COMPLETED,
        **fields,
    )
    for index, metrics in enumerate(row_metrics):
        EvalRunRow.objects.create(run=run, row_index=index, output="answer", state="done")
        EvalScore.objects.create(
            project=job.project,
            source_type=EvalScore.SourceType.DATASET_ROW,
            source_id=f"{run.cell_id}:{index}",
            run=run,
            score=sum(metrics.values()) / len(metrics),
            metrics={name: {"type": "llm", "score": value} for name, value in metrics.items()},
        )
    EvalRun.objects.filter(pk=run.pk).update(summary=runs.aggregate(run))
    run.refresh_from_db()
    return run


def _link(job, run, kind, **fields) -> FinetuningJobEval:
    return FinetuningJobEval.objects.create(
        job=job,
        eval_run=run,
        kind=kind,
        status=FinetuningJobEval.Status.RUNNING,
        model_id=fields.pop("model_id", "ft:model"),
        **fields,
    )


def test_the_job_score_is_the_mean_of_its_row_scores():
    _, _, job, _, _ = _setup()
    run = _scored_run(
        job, "final", [{"helpfulness": 0.8, "accuracy": 0.6}, {"helpfulness": 1.0, "accuracy": 1.0}]
    )
    _link(job, run, FinetuningJobEval.Kind.FINAL)
    [row] = sync_eval_scores(job)
    assert row["aggregate_score"] == pytest.approx(0.85)


def test_sync_eval_scores_computes_baseline_delta():
    _, _, job, _, _ = _setup()
    _link(job, _scored_run(job, "base", [{"m": 0.5}]), FinetuningJobEval.Kind.BASELINE)
    _link(
        job,
        _scored_run(job, "ckpt", [{"m": 0.7}]),
        FinetuningJobEval.Kind.CHECKPOINT,
        checkpoint_id="c2",
        checkpoint_step=2,
    )
    rows = sync_eval_scores(job)
    by_kind = {r["kind"]: r for r in rows}
    assert by_kind["baseline"]["aggregate_score"] == 0.5
    assert by_kind["checkpoint"]["aggregate_score"] == 0.7
    assert by_kind["checkpoint"]["baseline_delta"] == 0.2
    job.refresh_from_db()
    assert len(job.progress.get("judge_evals") or []) == 2


def test_sync_marks_all_errored_run_as_failed():
    """A completed run where every row errored surfaces FAILED, not a scoreless Completed."""
    _, _, job, _, _ = _setup()
    run = _scored_run(job, "final", [])
    EvalRunRow.objects.bulk_create(
        [EvalRunRow(run=run, row_index=i, error="provider down", state="done") for i in range(3)]
    )
    EvalRun.objects.filter(pk=run.pk).update(summary=runs.aggregate(run))
    _link(job, run, FinetuningJobEval.Kind.FINAL)
    final = next(r for r in sync_eval_scores(job) if r["kind"] == "final")
    assert final["status"] == FinetuningJobEval.Status.FAILED
    assert final["aggregate_score"] is None
    assert "errored" in (final["error_message"] or "")


def test_serialize_includes_per_metric_scores():
    _, _, job, _, _ = _setup()
    run = _scored_run(job, "base", [{"accuracy": 0.5, "faithfulness": 1.0}, {"accuracy": 1.0}])
    _link(job, run, FinetuningJobEval.Kind.BASELINE, model_id="openai/gpt-4o-mini")
    rows = serialize_job_evals(job)
    metrics = {m["name"]: m["score"] for m in rows[0]["metric_scores"]}
    assert metrics == {"accuracy": 0.75, "faithfulness": 1.0}
    assert rows[0]["sample_count"] == 2


def _label_dataset(project, name="labels"):
    return frozen_dataset(
        project,
        [
            {"input": f"txn {i}", "expected_output": label}
            for i, label in enumerate(["refund", "fraud", "refund", "chargeback"])
        ],
        name=name,
    )


def test_label_runs_carry_class_metrics_onto_the_job():
    _, _, job, _, _ = _setup()
    labels = _label_dataset(job.project)
    job.eval_dataset = labels
    job.save(update_fields=["eval_dataset"])
    run = EvalRun.objects.create(
        project=job.project,
        name="final",
        dataset=labels,
        cell=labels.active_cell,
        eval_set=job.eval_set,
        status=EvalRun.Status.RUNNING,
    )
    EvalRunRow.objects.bulk_create(
        [
            EvalRunRow(run=run, row_index=i, output=output, state="done")
            for i, output in enumerate(["Refund", "fraud", "fraud", "chargeback"])
        ]
    )
    _link(job, run, FinetuningJobEval.Kind.FINAL, model_id="ft:final")
    runs.finish(run)

    final_row = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.FINAL)
    blob = final_row.class_metrics
    assert blob["aggregates"]["accuracy"] == 0.75
    assert blob["confusion_matrix"]["labels"] == ["chargeback", "fraud", "refund"]
    assert {c["label"]: c["support"] for c in blob["classes"]} == {
        "chargeback": 1,
        "fraud": 1,
        "refund": 2,
    }
    job.refresh_from_db()
    assert job.progress["judge_evals"][-1]["class_metrics"] == blob


def test_class_metrics_only_for_label_references():
    _, _, job, _, _ = _setup()
    labels = _label_dataset(job.project)
    assert classes.is_classification(
        EvalRun.objects.create(
            project=job.project, name="r", dataset=labels, cell=labels.active_cell
        )
    )
    free_text = frozen_dataset(
        job.project,
        [
            {
                "input": f"q {i}",
                "expected_output": (
                    f"A long free-form explanation number {i} that goes on well past any "
                    "plausible label length and is unique every time."
                ),
            }
            for i in range(4)
        ],
        name="ft-refs",
    )
    run = EvalRun.objects.create(
        project=job.project, name="r2", dataset=free_text, cell=free_text.active_cell
    )
    assert classes.class_metrics(run) is None
    assert classes.class_metrics(EvalRun.objects.create(project=job.project, name="r3")) is None


def test_loss_curves_includes_judge_evals():
    u, _, job, _, _ = _setup()
    FinetuningJobEval.objects.create(
        job=job,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.COMPLETED,
        model_id=job.base_model,
        aggregate_score=0.42,
    )
    r = auth_client(u).get(reverse("finetuningjob-loss-curves", kwargs={"id": str(job.id)}))
    assert r.status_code == 200
    assert len(r.data["judge_evals"]) == 1
    assert r.data["judge_evals"][0]["aggregate_score"] == 0.42


def test_cancel_revokes_related_eval_runs(sft, fake_modal):
    u, _, job, _, _ = _setup()
    run = EvalRun.objects.create(
        project=job.project,
        name="live",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.RUNNING,
        celery_task_id="eval-celery-1",
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.RUNNING,
        model_id=job.base_model,
    )
    FinetuningJob.objects.filter(pk=job.pk).update(
        provider=FinetuningJob.Provider.MODAL,
        remote_job_id="run-x:fc-x",
        requested_configuration={"runtime": training_release.current()},
    )
    fake_modal.adopt("fc-x", "sft_train")
    with patch("celery.current_app.control.revoke") as revoke:
        r = auth_client(u).post(reverse("finetuningjob-cancel", kwargs={"id": job.id}))
    assert r.status_code == 200
    assert revoke.call_args.args[0] == ["eval-celery-1"]
    assert [call for call, _ in fake_modal.cancelled] == ["fc-x"]
    run.refresh_from_db()
    assert run.status == EvalRun.Status.CANCELLED
    row = FinetuningJobEval.objects.get(job=job)
    assert row.status == FinetuningJobEval.Status.CANCELLED


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_group_jobs_share_one_baseline_eval_run(django_capture_on_commit_callbacks):
    _, p, job_a, eval_ds, eset = _setup()
    _basetenify(job_a)
    gid = uuid.uuid4()
    job_a.group_id = gid
    job_a.baseline_model = "openai/gpt-5.6-sol"
    job_a.save(update_fields=["group_id", "baseline_model"])

    job_b = FinetuningJob.objects.create(
        project=p,
        capability=job_a.capability,
        dataset=job_a.dataset,
        eval_dataset=eval_ds,
        eval_set=eset,
        base_model="meta-llama/Llama-3.1-8B-Instruct",
        eval_incumbent_before=True,
        eval_model_before=False,
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        group_id=gid,
        baseline_model="openai/gpt-5.6-sol",
        triggered_by=job_a.triggered_by,
    )
    job_c = FinetuningJob.objects.create(
        project=p,
        capability=job_a.capability,
        dataset=job_a.dataset,
        eval_dataset=eval_ds,
        eval_set=eset,
        base_model="mistralai/Mistral-7B-Instruct-v0.3",
        eval_incumbent_before=True,
        eval_model_before=False,
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        group_id=gid,
        baseline_model="openai/gpt-5.6-sol",
        triggered_by=job_a.triggered_by,
    )

    with (
        patch("overbae.tasks.innermind.run_eval_run.delay") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id="celery-shared-base")
        tick_job_evals(job_a)
        tick_job_evals(job_b)
        tick_job_evals(job_c)

    assert apply.call_count == 1  # one EvalRun, not three
    rows = list(
        FinetuningJobEval.objects.filter(
            job__in=[job_a, job_b, job_c], kind=FinetuningJobEval.Kind.BASELINE
        )
    )
    assert len(rows) == 3
    assert len({r.eval_run_id for r in rows}) == 1
    assert all(r.model_id == "openai/gpt-5.6-sol" for r in rows)


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_cancel_leaves_shared_group_baseline_running():
    from overbae.services.finetuning_eval import cancel_related_evals

    u, p, job_a, eval_ds, eset = _setup()
    gid = uuid.uuid4()
    job_a.group_id = gid
    job_a.save(update_fields=["group_id"])
    job_b = FinetuningJob.objects.create(
        project=p,
        capability=job_a.capability,
        dataset=job_a.dataset,
        eval_dataset=eval_ds,
        eval_set=eset,
        base_model="other/model",
        status=FinetuningJob.Status.RUNNING,
        provider=FinetuningJob.Provider.BASETEN,
        group_id=gid,
        triggered_by=u,
    )
    run = EvalRun.objects.create(
        project=p,
        name="shared-base",
        dataset=eval_ds,
        cell=eval_ds.active_cell,
        eval_set=eset,
        status=EvalRun.Status.RUNNING,
        celery_task_id="eval-shared-1",
    )
    FinetuningJobEval.objects.create(
        job=job_a,
        eval_run=run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="openai/gpt-5.6-sol",
    )
    FinetuningJobEval.objects.create(
        job=job_b,
        eval_run=run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="openai/gpt-5.6-sol",
    )

    cancel_related_evals(job_a)

    run.refresh_from_db()
    assert run.status == EvalRun.Status.RUNNING
    assert FinetuningJobEval.objects.get(job=job_a).status == FinetuningJobEval.Status.CANCELLED
    assert FinetuningJobEval.objects.get(job=job_b).status == FinetuningJobEval.Status.RUNNING


def _basetenify(job):
    job.provider = FinetuningJob.Provider.BASETEN
    job.base_model = "Qwen/Qwen3-8B"
    job.remote_job_id = f"proj-1:{job.id}"
    job.save(update_fields=["provider", "base_model", "remote_job_id"])
    return job


def _resolved_base(base: str) -> str:
    """The catalog remaps a base to its unsloth mirror, and the deploy task slugs the resolved
    id — so a fixture that slugs the raw id silently stops matching."""
    from overbae.modal.model_registry import get_hf_base

    return get_hf_base(base)


def _base_slug(base: str) -> str:
    from overbae.services.deployment import base_model_slug

    return base_model_slug(_resolved_base(base))


def _ready_base_deployment(project, base="Qwen/Qwen3-8B"):
    from overbae.models import DeployedModel

    hf_base = _resolved_base(base)
    return DeployedModel.objects.create(
        project=project,
        finetuning_job=None,
        model_id=_base_slug(base),
        base_model_id=hf_base,
        status=DeployedModel.Status.READY,
        max_model_len=16384,
        inference_url="https://example--vllm-base.modal.run/v1",
    )


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_baseten_baseline_uses_an_existing_ready_base_deployment(
    django_capture_on_commit_callbacks, fake_llm
):
    fake_llm.catalog_models = []
    _, _, job, _, _ = _setup()
    _basetenify(job)

    dep = _ready_base_deployment(job.project)
    # The enqueue is deferred to transaction.on_commit — execute the callbacks.
    with (
        patch("overbae.tasks.innermind.run_eval_run.delay") as apply2,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply2.return_value = MagicMock(id="celery-eval-b1")
        tick_job_evals(job)

    assert apply2.call_count == 1
    row = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.BASELINE)
    assert row.model_id == dep.model_id
    assert row.status == FinetuningJobEval.Status.RUNNING
    # Route via the inference gateway, not the worker inference_url — its query string
    # breaks the OpenAI client's /chat/completions suffix.
    ref = row.eval_run.model_ref
    assert ref.base_url == f"{settings.INFERENCE_API_URL.rstrip('/')}/v1"
    assert "?" not in ref.base_url
    assert ref.api_key_ref == "INFERENCE_API_KEY"

    # Idempotent — a second tick doesn't duplicate the baseline.
    with patch("overbae.tasks.innermind.run_eval_run.delay") as apply3:
        tick_job_evals(job)
    assert apply3.call_count == 0
    assert FinetuningJobEval.objects.filter(job=job).count() == 1


def test_baseline_delta_computes_when_baseline_lands_after_final():
    _, _, job, _, _ = _setup()
    _basetenify(job)
    final_run = EvalRun.objects.create(
        project=job.project,
        name="final",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.COMPLETED,
        summary={"score": 0.9},
    )
    base_run = EvalRun.objects.create(
        project=job.project,
        name="base",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.RUNNING,  # still scoring
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=final_run,
        kind=FinetuningJobEval.Kind.FINAL,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="ft-x",
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=base_run,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="base--qwen--qwen3-8b",
    )

    rows = {r["kind"]: r for r in sync_eval_scores(job)}
    assert rows["final"]["aggregate_score"] == 0.9
    assert rows["final"]["baseline_delta"] is None  # baseline not in yet

    EvalRun.objects.filter(pk=base_run.pk).update(
        status=EvalRun.Status.COMPLETED,
        summary={"score": 0.6},
    )
    rows = {r["kind"]: r for r in sync_eval_scores(job)}
    assert rows["baseline"]["aggregate_score"] == 0.6
    assert rows["final"]["baseline_delta"] == pytest.approx(0.3)


def _fake_modal(monkeypatch, calls):
    import modal

    class FakeRegister:
        def __call__(self):
            return self

        class register_base:  # noqa: N801 — mimics modal method handle
            @staticmethod
            def remote(**kw):
                calls.append(("register_base", kw))
                return {"weights_path": "/weights/base--qwen--qwen3-8b", "ready": True}

    class FakeInference:
        def __call__(self):
            return self

        class register_model:  # noqa: N801
            @staticmethod
            def remote(**kw):
                calls.append(("register_model", kw))
                return "https://example--vllm-base.modal.run/v1"

    def fake_cls(app, name, environment_name=None):
        return FakeRegister() if app == "overmind-register" else FakeInference()

    class FakePreWarm:
        @staticmethod
        def remote(**kw):
            calls.append(("pre_warm", kw))

    monkeypatch.setattr(modal.Cls, "from_name", fake_cls)
    monkeypatch.setattr(modal.Function, "from_name", lambda *a, **kw: FakePreWarm())


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_deploy_base_model_for_eval_does_not_provision_an_unavailable_route(
    monkeypatch, django_capture_on_commit_callbacks, fake_llm
):
    fake_llm.catalog_models = []
    from overbae.models import DeployedModel
    from overbae.tasks.model_deployment import deploy_base_model_for_eval

    _, _, job, _, _ = _setup()
    _basetenify(job)

    calls: list = []
    _fake_modal(monkeypatch, calls)
    with (
        patch("overbae.tasks.innermind.run_eval_run.delay") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id="celery-eval-b2")
        deploy_base_model_for_eval(job_id=str(job.id))

    assert calls == []
    assert not DeployedModel.objects.exists()
    assert apply.call_count == 0


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_deploy_base_model_dedupes_ready_deployment(
    monkeypatch, django_capture_on_commit_callbacks, fake_llm
):
    fake_llm.catalog_models = []
    from overbae.tasks.model_deployment import deploy_base_model_for_eval

    _, _, job, _, _ = _setup()
    _basetenify(job)
    _ready_base_deployment(job.project)

    calls: list = []
    _fake_modal(monkeypatch, calls)
    with (
        patch("overbae.tasks.innermind.run_eval_run.delay") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id="celery-eval-b3")
        deploy_base_model_for_eval(job_id=str(job.id))

    assert calls == []  # no deploy work
    assert apply.call_count == 1  # the existing ready route can start immediately
    from overbae.models import DeployedModel

    assert not DeployedModel.objects.get(model_id=_base_slug(job.base_model)).deployment_notify


def test_deploy_base_model_skips_cancelled_job(monkeypatch):
    from overbae.models import DeployedModel
    from overbae.tasks.model_deployment import deploy_base_model_for_eval

    _, _, job, _, _ = _setup()
    _basetenify(job)
    job.status = FinetuningJob.Status.CANCELLED
    job.save(update_fields=["status"])

    calls: list = []
    _fake_modal(monkeypatch, calls)
    with patch("overbae.tasks.innermind.run_eval_run.delay") as apply:
        deploy_base_model_for_eval(job_id=str(job.id))

    assert calls == []
    assert apply.call_count == 0
    assert DeployedModel.objects.count() == 0


@override_settings(INFERENCE_API_URL="https://gateway.example.modal.run")
def test_baseten_final_eval_fires_after_ready_deployment(
    django_capture_on_commit_callbacks, fake_llm
):
    fake_llm.catalog_models = []
    from overbae.models import DeployedModel

    _, _, job, _, _ = _setup()
    _basetenify(job)
    job.output_model_name = "baseten/abc/final"
    job.status = FinetuningJob.Status.SUCCEEDED
    job.save(update_fields=["output_model_name", "status"])

    DeployedModel.objects.create(
        project=job.project,
        finetuning_job=job,
        model_id="ft-abc-qwen3-8b",
        base_model_id=job.base_model,
        status=DeployedModel.Status.READY,
        inference_url="https://example--vllm.modal.run/v1",
    )

    with (
        patch("overbae.tasks.innermind.run_eval_run.delay") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id="celery-eval-t1")
        tick_job_evals(job)

    assert apply.call_count == 1
    final = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.FINAL)
    # Eval targets the served vLLM model id, not the synthetic baseten name.
    assert final.model_id == "ft-abc-qwen3-8b"
    assert final.status == FinetuningJobEval.Status.RUNNING
    ref = final.eval_run.model_ref
    assert ref.base_url == f"{settings.INFERENCE_API_URL.rstrip('/')}/v1"
    assert ref.api_key_ref == "INFERENCE_API_KEY"
