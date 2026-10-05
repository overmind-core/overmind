from types import SimpleNamespace

import pytest
from asgiref.sync import async_to_sync


def drain_stream(response) -> bytes:
    """Collect a streaming response body in a sync test.

    SSE views hand `StreamingHttpResponse` an async iterable so ASGI writes chunks as they
    are produced, which leaves `streaming_content` un-joinable from sync code.
    """
    if not response.streaming:
        return response.content
    content = response.streaming_content
    if not hasattr(content, "__aiter__"):
        return b"".join(content)

    async def _collect():
        return [chunk async for chunk in content]

    return b"".join(async_to_sync(_collect)())


@pytest.fixture(autouse=True)
def _clerk_offline(monkeypatch):
    """ClerkAuthentication is the first authenticator, so every Bearer request verifies
    the token against Clerk's remote JWKS over real HTTP; offline runs hang there.
    Patch the call, not the class, to keep authenticator order and the 401 path intact.
    """
    monkeypatch.setattr(
        "overbae.auth.authenticate_request",
        lambda request, options: SimpleNamespace(is_signed_in=False),
    )


@pytest.fixture(autouse=True)
def _offline_model_resolution(monkeypatch):
    """Model resolution must behave the same with and without real credentials:
    a developer's real OPENROUTER_API_KEY would let an unmocked judge call
    reach the provider while CI fails on the missing key. Every test gets the
    same inert key; tests of the no-key path delete it themselves.
    """
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    monkeypatch.setattr("overbae.services.llm_context.fetch_model_catalog", lambda: ([], False))
    monkeypatch.setattr(
        "overbae.services.eval.context_suggestions.fetch_model_catalog", lambda: ([], False)
    )
    from overbae.core.decisions import DecisionError

    def no_decision_network(*args, **kwargs):
        raise DecisionError("offline_test")

    monkeypatch.setattr("overbae.core.decisions._request", no_decision_network)
    monkeypatch.setattr(
        "overbae.services.finetuning_eval.resolve_training_openrouter_slug", lambda _: None
    )
    monkeypatch.setattr(
        "overbae.services.deployment.resolve_training_openrouter_slug", lambda _: None
    )


@pytest.fixture(autouse=True)
def _offline_compute_prices(monkeypatch):
    monkeypatch.setattr(
        "overbae.services.provider_pricing.current_rates",
        lambda: {
            "source": "offline-test-fixture",
            "status": "current",
            "fetched_at": "2026-10-04T00:00:00+00:00",
            "rates": {
                "gpu_hour_cost_h100": 3.95,
                "gpu_hour_cost_h200": 4.54,
                "cpu_hour_cost": 0.0473,
                "mem_gib_hour_cost": 0.008,
            },
        },
    )


@pytest.fixture(autouse=True)
def _commercial_billing(settings):
    """Remaining-credit billing is on in tests unless a case clears the key."""
    settings.STRIPE_SECRET_KEY = "sk_test_billing"


EVAL_ROWS = [
    {"input": "q1", "expected_output": "a1"},
    {"input": "q2", "expected_output": "a2"},
]
TRAIN_ROWS = [
    {"messages": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]},
    {"messages": [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]},
]


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    """Every test writes dataset files under its own tmp dir."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _inline_dataset_tasks(monkeypatch):
    """Landing and runs execute in-process: no worker in tests, and the API's
    202 answers still leave a finished version behind."""
    from overbae.tasks import datasets as dataset_tasks

    for task in (dataset_tasks.land, dataset_tasks.run):
        monkeypatch.setattr(
            task, "apply_async", lambda kwargs, _t=task, **_: _t.apply(kwargs=kwargs)
        )
    # The agent needs Cursor; a test that wants a turn drives the agent module itself.
    # Landing hands the dataset to its first scan, so the stub ends that scan.
    from overbae.services.datasets.notebook import agent

    monkeypatch.setattr(
        dataset_tasks.diagnose,
        "apply_async",
        lambda kwargs, **_: agent.settle(kwargs["dataset_id"]),
    )
    monkeypatch.setattr(dataset_tasks.turn, "apply_async", lambda kwargs, **_: None)


def _lift_messages(row):
    """The old canonical shape kept the transcript under ``input``; the product
    table carries it as a ``messages`` column."""
    row = dict(row)
    inp = row.get("input")
    if isinstance(inp, dict) and isinstance(inp.get("messages"), list) and "messages" not in row:
        row["messages"] = inp["messages"]
        if inp.get("tools"):
            row["tools"] = inp["tools"]
        rest = {k: v for k, v in inp.items() if k not in ("messages", "tools")}
        if rest:
            row["input"] = rest
        else:
            row.pop("input")
        if row.get("expected_output") is None:
            row.pop("expected_output", None)
    return row


def frozen_dataset(project, rows=None, *, capability=None, name="ds", contract=None, user=None):
    """A dataset whose source landed in-process, so its active version is
    ready. ``rows`` defaults to a two-row table of the requested intent."""
    from overbae.models import Dataset
    from overbae.services.datasets import land
    from overbae.services.datasets.notebook import run as run_svc

    if rows is None:
        rows = TRAIN_ROWS if contract == "train" else EVAL_ROWS
    dataset = Dataset.objects.create(
        project=project, capability=capability, name=name, intent=contract or "pending"
    )
    land.land_rows(dataset, [_lift_messages(r) for r in rows], user=user)
    run_svc.execute(dataset, user=user)
    dataset.refresh_from_db()
    review_fixture(dataset)
    return dataset


def plan_fixture(dataset, cell=None):
    from overbae.services.datasets.preparation import PlanRequest, save_plan

    cell = cell or dataset.active_cell
    plan = PlanRequest.model_validate(
        {
            "version": str(cell.id),
            "objective": "Prepare the controlled fixture for its declared task.",
            "consumer": "custom",
            "understanding": "Fixture values and intended task are supplied by the test.",
            "families": [{"name": "Fixture", "evidence": "Controlled inputs and references."}],
            "steps": [
                {
                    "id": "prepare",
                    "kind": "transform",
                    "description": "Apply the declared fixture transformation.",
                }
            ],
            "checks": [
                {
                    "name": name,
                    "category": category,
                    "method": "deterministic",
                    "question": "Does the fixture satisfy " + name + "?",
                }
                for name, category in {
                    "task_alignment": "semantic",
                    "input_evidence": "preservation",
                    "answer_support": "semantic",
                    "output_schema": "technical",
                }.items()
            ],
        }
    )
    return save_plan(dataset, cell, plan)


def review_fixture(dataset, cell=None):
    from overbae.services.datasets import review

    cell = cell or dataset.active_cell
    plan_fixture(dataset, cell)
    review.record_quality(
        dataset,
        cell,
        [
            {
                "name": name,
                "result": "pass",
                "evidence": "Known test fixture.",
                "rows_checked": cell.rows,
            }
            for name in ("task_alignment", "input_evidence", "answer_support", "output_schema")
        ],
        script="df = pd.DataFrame({name: [True] * len(df) for name in ('task_alignment', 'input_evidence', 'answer_support', 'output_schema')})",
    )


@pytest.fixture(autouse=True)
def _offline_rubric_compiler(monkeypatch):
    """Authoring a judge compiles its rubric, so every test that saves one would
    otherwise reach a provider and wait out the retry budget before falling back.
    Returns a fixed two-item checklist; the compiler's own tests opt back in.
    """
    from overbae.services.eval import rubric_compiler

    monkeypatch.setattr(
        rubric_compiler,
        "compile_rubric",
        lambda rubric_md, **kwargs: {
            "checklist": [
                {"id": "criterion_1", "q": "Does the output satisfy the rubric?", "weight": 0.5},
                {"id": "criterion_2", "q": "Is the output free of errors?", "weight": 0.5},
            ],
            "variables": ["input", "output"],
        },
    )


@pytest.fixture
def make_dataset(settings, tmp_path):
    """Land rows and run the chain in-process, so a test gets a version back
    synchronously without a worker. ``cells`` is a list of ``(title, script)``."""

    def _make(
        project,
        rows,
        *,
        name="rows",
        capability=None,
        cells=None,
        run=True,
        user=None,
        source_kind="file",
        target=None,
    ):
        from overbae.models import Dataset
        from overbae.services.datasets import land, lifecycle
        from overbae.services.datasets.notebook import run as run_svc

        dataset = Dataset.objects.create(
            project=project,
            capability=capability,
            name=name,
            source_kind=source_kind,
            intent=target or "pending",
        )
        land.land_rows(dataset, list(rows), user=user)
        for title, script in cells or []:
            lifecycle.add_cell(dataset, title=title, script=script, user=user)
        if run:
            run_svc.execute(dataset, user=user)
        dataset.refresh_from_db()
        return dataset

    return _make
