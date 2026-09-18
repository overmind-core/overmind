from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from conftest import frozen_dataset
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import (
    Capability,
    Dataset,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.datasets import rows as row_store
from overbae.services.finetuning_pricing import (
    estimate_training_cost,
    estimate_training_time_s,
    humanize_duration,
    training_price_per_million,
)
from overbae.services.recommendation.analysis import build_analysis
from overbae.services.recommendation.capability_context import collect_capability_context

pytestmark = pytest.mark.django_db

CELERY_PATH = "overbae.tasks.finetuning.run_finetuning.apply_async"


def _user() -> User:
    return User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="pass",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
        projects_limit=5,
    )


def _auth_client(user: User) -> APIClient:
    client = APIClient()
    token = RefreshToken.for_user(user)
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
    return client


def _setup() -> tuple[User, Project, Capability]:
    u = _user()
    p = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=u, project=p)
    slug = f"a-{uuid.uuid4().hex[:8]}"
    a = Capability.objects.create(project=p, name=slug, slug=slug)
    return u, p, a


def _dataset(
    project: Project, capability: Capability, *, intent: str, trace_ids: list[str]
) -> Dataset:
    rows = []
    for i, tid in enumerate(trace_ids):
        row = {
            "messages": [
                {"role": "user", "content": f"q{i}"},
                {"role": "assistant", "content": f"a{i}"},
            ]
        }
        if intent == "eval":
            row = {"input": f"q{i}", "expected_output": f"a{i}"}
        if tid:
            row["trace_id"] = tid
        rows.append(row)
    return frozen_dataset(
        project, rows, capability=capability, name=f"{intent}-{uuid.uuid4().hex[:6]}"
    )


def test_together_price_tiers_match_published_rates():
    # together.ai/pricing SFT rates (retrieved 2026-07-13)
    assert training_price_per_million(8, use_lora=True, backend="together") == 0.48
    assert training_price_per_million(8, use_lora=False, backend="together") == 0.54
    assert training_price_per_million(32, use_lora=True, backend="together") == 1.50
    assert training_price_per_million(70, use_lora=True, backend="together") == 2.90
    assert training_price_per_million(70, use_lora=False, backend="together") == 3.20
    # >100B models are individually priced.
    assert training_price_per_million(120, use_lora=True, backend="together") is None


def test_baseten_bills_per_gpu_minute_not_per_token():
    assert training_price_per_million(8, use_lora=True, backend="baseten") is None
    est = estimate_training_cost(1_000_000, total_params_b=8, use_lora=True, backend="baseten")
    assert est is not None
    assert est["usd"] > 0


def test_cost_estimate_applies_together_minimum_charge():
    tiny = estimate_training_cost(1000, total_params_b=8, use_lora=True, backend="together")
    assert tiny is not None
    assert tiny["minimum_applied"] is True
    assert tiny["usd"] == 4.00

    big = estimate_training_cost(100_000_000, total_params_b=8, use_lora=True, backend="together")
    assert big is not None
    assert big["minimum_applied"] is False
    assert big["usd"] == pytest.approx(48.0)


def test_cost_estimate_none_for_unpriced_backend_or_empty():
    assert estimate_training_cost(0, total_params_b=8, use_lora=True, backend="together") is None
    # >100B Together models are individually priced.
    assert (
        estimate_training_cost(1000, total_params_b=120, use_lora=True, backend="together") is None
    )


def test_time_estimate_scales_with_model_size_and_tokens():
    small = estimate_training_time_s(1_000_000, total_params_b=1, use_lora=True)
    large = estimate_training_time_s(1_000_000, total_params_b=70, use_lora=True)
    more_tokens = estimate_training_time_s(10_000_000, total_params_b=70, use_lora=True)
    full_ft = estimate_training_time_s(1_000_000, total_params_b=70, use_lora=False)
    assert small < large < more_tokens
    assert full_ft > large  # 6·N·D vs 4·N·D
    # Overhead floor: even a zero-token run isn't instant.
    assert estimate_training_time_s(0, total_params_b=1, use_lora=True) >= 600


def test_humanize_duration():
    assert humanize_duration(30) == "<1 min"
    assert humanize_duration(720) == "12 min"
    assert humanize_duration(3900) == "1 h 05 min"
    assert humanize_duration(2 * 86400 + 3 * 3600) == "2 d 3 h"


def test_estimate_training_run_uses_epochs_and_lora():
    from overbae.services.finetuning_pricing import estimate_training_run

    one = estimate_training_run(
        dataset_tokens=10_000,
        n_epochs=1,
        total_params_b=8,
        use_lora=True,
        backend="together",
    )
    three = estimate_training_run(
        dataset_tokens=10_000,
        n_epochs=3,
        total_params_b=8,
        use_lora=True,
        backend="together",
    )
    full = estimate_training_run(
        dataset_tokens=10_000,
        n_epochs=1,
        total_params_b=8,
        use_lora=False,
        backend="together",
    )
    assert three["trained_tokens"] == one["trained_tokens"] * 3
    assert three["time_estimate"]["seconds"] > one["time_estimate"]["seconds"]
    assert full["time_estimate"]["seconds"] >= one["time_estimate"]["seconds"]
    assert one["cost_estimate"] is not None
    assert three["cost_estimate"]["usd"] >= one["cost_estimate"]["usd"]


_STATS = {
    "num_examples": 500,
    "avg_input_chars": 300,
    "avg_output_chars": 60,
    "has_tool_calling": False,
    "max_token_length": 200,
}


def _analysis(**kwargs):
    return build_analysis(
        _STATS, task_type="classification", task_type_source="heuristic", **kwargs
    )


def test_analysis_builds_costed_candidates():
    analysis = _analysis()

    candidates = analysis["candidates"]
    assert candidates
    assert sum(r["selected"] for r in candidates) == 1
    assert analysis["shown"][0] == next(r["model"] for r in candidates if r["selected"])

    # dataset tokens: 500 × 360 chars / 3 chars-per-token = 60 000
    assert analysis["dataset"]["total_tokens"] == 60_000

    for r in candidates:
        assert r["gpu_config"]["gpu_type"]
        assert r["gpu_config"]["num_gpus"] == 1
        assert r["time_estimate"]["seconds"] > 0
        assert r["time_estimate"]["human"]
        assert "total_params_b" in r
        assert isinstance(r["training_type"]["lora"]["enabled"], bool)
        assert isinstance(r["training_type"]["full"]["enabled"], bool)
        epochs = r["hyperparams"]["n_epochs"]
        if r["cost_estimate"] is not None:
            assert r["cost_estimate"]["trained_tokens"] == 60_000 * epochs


def test_recommended_hyperparams_scale_with_dataset_size():
    from django.test import override_settings

    from overbae.services.recommendation.hyperparams import (
        compute_hyperparams,
        hyperparam_provenance,
    )

    entry = {"min_batch_size": 1, "max_batch_size": 32, "total_params_b": 8.0}
    with override_settings(FINETUNING_BACKEND="baseten"):
        thousands = compute_hyperparams(5_000, model_entry=entry)
        large = compute_hyperparams(19_704, model_entry=entry)

        assert thousands["batch_size"] == 10  # 0.2% of 5 000
        assert large["batch_size"] == 32  # 0.2% of 19 704, capped at catalog max
        assert large["n_epochs"] == 2  # ≥10k rows: fewer passes

        steps = (19_704 // large["batch_size"]) * large["n_epochs"]
        assert steps < 2_000

        reasons = hyperparam_provenance(
            large, num_examples=19_704, params_b=8.0, use_lora=True, backend="baseten"
        )
        assert "optimizer steps" in reasons["batch_size"]
        assert "at batch 1" in reasons["batch_size"]
        assert "large dataset" in reasons["n_epochs"]


def test_analysis_embeds_capability_context():
    _, _, capability = _setup()[0:3]
    ctx = collect_capability_context(capability)
    analysis = _analysis(capability_context=ctx)
    assert analysis["capability_context"]["capability_id"] == str(capability.id)
    assert analysis["capability_context"]["traces"]["n"] == 0


def test_collect_capability_context_reads_usage_stats():
    _, _, capability = _setup()
    capability.usage_stats = {
        "llm_calls": 42,
        "tool_calls": 7,
        "prompt_tokens": 1000,
        "completion_tokens": 200,
        "models": {"gpt-4o": 42},
    }
    capability.save(update_fields=["usage_stats"])
    ctx = collect_capability_context(capability)
    assert ctx["usage"]["llm_calls"] == 42
    assert ctx["usage"]["tool_calls"] == 7
    assert ctx["usage"]["models_used"] == {"gpt-4o": 42}


def test_overlapping_trace_ids_intersects_on_source_trace():
    _, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1", "t2", "t3", ""])
    eval_ds = _dataset(p, a, intent="eval", trace_ids=["t2", "t3", "t4"])
    overlap = row_store.trace_ids(train.active_cell) & row_store.trace_ids(eval_ds.active_cell)
    assert overlap == {"t2", "t3"}
    # Blank trace ids never match.
    blank_train = _dataset(p, a, intent="ft", trace_ids=["", ""])
    assert (
        row_store.trace_ids(blank_train.active_cell) & row_store.trace_ids(eval_ds.active_cell)
        == set()
    )


def test_dataset_overlap_endpoint():
    u, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1", "t2", "t3"])
    eval_ds = _dataset(p, a, intent="eval", trace_ids=["t3", "t4"])

    r = _auth_client(u).get(
        reverse("finetuningjob-dataset-overlap"),
        {"dataset": str(train.id), "eval_dataset": str(eval_ds.id)},
    )
    assert r.status_code == 200, r.data
    assert r.data == {"overlap_count": 1, "train_total": 3, "basis": "trace_id"}


def test_dataset_overlap_endpoint_scopes_to_user_projects():
    u, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1"])
    _u2, p2, a2 = _setup()
    foreign_eval = _dataset(p2, a2, intent="eval", trace_ids=["t1"])

    r = _auth_client(u).get(
        reverse("finetuningjob-dataset-overlap"),
        {"dataset": str(train.id), "eval_dataset": str(foreign_eval.id)},
    )
    assert r.status_code == 404


def test_job_create_persists_eval_dataset_and_eval_set():
    u, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1", "t2"])
    eval_ds = _dataset(p, a, intent="eval", trace_ids=["t3"])
    eval_set = EvalSet.objects.create(project=p, capability=a, name="active set")
    ev = Evaluator.objects.create(
        project=p,
        capability=a,
        name="gate",
        kind=Evaluator.Kind.DETERMINISTIC,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=ev, role=EvalSetMember.Role.GENERATIVE, order=0
    )

    with patch(CELERY_PATH, return_value=type("R", (), {"id": "task-1"})()):
        r = _auth_client(u).post(
            reverse("finetuningjob-list"),
            {
                "project": str(p.id),
                "capability": str(a.id),
                "dataset": str(train.id),
                "eval_dataset": str(eval_ds.id),
                "eval_set": str(eval_set.id),
                "name": "with-eval",
                "base_model": "meta-llama/Llama-3.2-3B-Instruct",
                "hyperparameters": {"n_epochs": 1},
            },
            format="json",
        )
    assert r.status_code == 201, r.data
    job = FinetuningJob.objects.get(pk=r.data["id"])
    assert job.eval_dataset_id == eval_ds.id
    assert job.eval_set_id == eval_set.id


def test_job_create_rejects_eval_set_with_no_generative_members():
    u, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1"])
    eval_ds = _dataset(p, a, intent="eval", trace_ids=["t2"])
    eval_set = EvalSet.objects.create(project=p, capability=a, name="empty set")

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(u).post(
            reverse("finetuningjob-list"),
            {
                "project": str(p.id),
                "capability": str(a.id),
                "dataset": str(train.id),
                "eval_dataset": str(eval_ds.id),
                "eval_set": str(eval_set.id),
                "name": "empty-eval-set",
                "base_model": "meta-llama/Llama-3.2-3B-Instruct",
                "hyperparameters": {"n_epochs": 1},
            },
            format="json",
        )
    assert r.status_code == 400
    assert "eval_set" in r.data
    mock_apply.assert_not_called()


def test_job_create_rejects_non_eval_intent_eval_dataset():
    u, p, a = _setup()
    train = _dataset(p, a, intent="ft", trace_ids=["t1"])
    wrong = _dataset(p, a, intent="ft", trace_ids=["t2"])

    with patch(CELERY_PATH) as mock_apply:
        r = _auth_client(u).post(
            reverse("finetuningjob-list"),
            {
                "project": str(p.id),
                "dataset": str(train.id),
                "eval_dataset": str(wrong.id),
                "name": "bad-eval",
                "base_model": "meta-llama/Llama-3.2-3B-Instruct",
            },
            format="json",
        )
    assert r.status_code == 400
    assert "eval_dataset" in r.data
    mock_apply.assert_not_called()


def test_recommend_endpoint_answers_the_same_way_twice():
    u, p, a = _setup()
    ds = _dataset(p, a, intent="ft", trace_ids=["t1"])

    client = _auth_client(u)
    payload = {"dataset_id": str(ds.id), "capability_id": str(a.id)}
    first = client.post(reverse("finetuningjob-recommend"), payload, format="json")
    second = client.post(reverse("finetuningjob-recommend"), payload, format="json")

    assert first.status_code == 200, first.data
    assert second.status_code == 200, second.data
    assert first.data["task_type"]
    assert [c["model"] for c in first.data["candidates"]] == [
        c["model"] for c in second.data["candidates"]
    ]
    assert first.data["capability_context"]["capability_id"] == str(a.id)


def test_estimate_endpoint_scales_with_epochs_and_lora():
    from overbae.services.recommendation import find_catalog_model, tier_models

    u, p, a = _setup()
    ds = _dataset(p, a, intent="ft", trace_ids=["t1", "t2", "t3"])
    # Seed real stats so token estimate is non-zero without needing datapoint rows.
    version = ds.active_cell
    version.stats = {
        "num_examples": 50_000,
        "avg_input_chars": 900,
        "avg_output_chars": 300,
        "has_tool_calling": False,
        "max_token_length": 400,
    }
    version.save(update_fields=["stats"])

    catalog = tier_models()
    model = next(
        m
        for ms in catalog.values()
        for m in ms
        if (m.get("training_type") or {}).get("lora", {}).get("enabled", False)
    )
    assert find_catalog_model(model["id"]) is not None

    client = _auth_client(u)
    lora_1 = client.post(
        reverse("finetuningjob-estimate"),
        {
            "dataset_id": str(ds.id),
            "base_model": model["id"],
            "n_epochs": 1,
            "use_lora": True,
        },
        format="json",
    )
    lora_3 = client.post(
        reverse("finetuningjob-estimate"),
        {
            "dataset_id": str(ds.id),
            "base_model": model["id"],
            "n_epochs": 3,
            "use_lora": True,
        },
        format="json",
    )
    full_1 = client.post(
        reverse("finetuningjob-estimate"),
        {
            "dataset_id": str(ds.id),
            "base_model": model["id"],
            "n_epochs": 1,
            "use_lora": False,
        },
        format="json",
    )
    assert lora_1.status_code == 200, lora_1.data
    assert lora_3.status_code == 200, lora_3.data
    assert full_1.status_code == 200, full_1.data
    assert lora_3.data["trained_tokens"] == lora_1.data["trained_tokens"] * 3
    assert lora_3.data["time_estimate"]["seconds"] > lora_1.data["time_estimate"]["seconds"]
    assert full_1.data["time_estimate"]["seconds"] >= lora_1.data["time_estimate"]["seconds"]
    # Cost may be None for unpriced backends/sizes — only assert when both priced.
    if lora_1.data["cost_estimate"] and lora_3.data["cost_estimate"]:
        assert lora_3.data["cost_estimate"]["usd"] >= lora_1.data["cost_estimate"]["usd"]


def test_model_defaults_endpoint_derives_dataset_aware_hyperparams():
    from overbae.services.recommendation import tier_models

    u, p, a = _setup()
    ds = _dataset(p, a, intent="ft", trace_ids=["t1"])
    version = ds.active_cell
    version.stats = {
        "num_examples": 19_704,
        "avg_input_chars": 900,
        "avg_output_chars": 300,
        "has_tool_calling": False,
        "max_token_length": 400,
    }
    version.save(update_fields=["stats"])

    model = next(m for ms in tier_models().values() for m in ms)
    client = _auth_client(u)
    r = client.post(
        reverse("finetuningjob-model-defaults"),
        {"dataset_id": str(ds.id), "base_model": model["id"]},
        format="json",
    )
    assert r.status_code == 200, r.data
    hp = r.data["hyperparams"]
    assert hp["batch_size"] > 1
    assert hp["batch_size"] <= (model.get("max_batch_size") or 64)
    assert hp["n_epochs"] <= 2  # ≥10k rows → few passes
    assert r.data["selected"] is False
    assert r.data["hyperparam_reasons"]["batch_size"]
    assert r.data["learning_rate_lora"] > 0

    unknown = client.post(
        reverse("finetuningjob-model-defaults"),
        {"dataset_id": str(ds.id), "base_model": "not/a-model"},
        format="json",
    )
    assert unknown.status_code == 400
