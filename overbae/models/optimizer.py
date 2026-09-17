# ruff: noqa: UP037
"""Capability optimizer: experiment / iteration / candidate scoring.

The client posts candidates and datapoint outputs. Server runs EvalRuns and
records scores. ``run_experiment_advance`` only drives EVALUATING_* wait-states.
"""

from __future__ import annotations

import json
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from random import randint

from celery import shared_task
from django.conf import settings
from django.db import models, transaction

from overbae.services.eval import funnel as judging
from overbae.services.eval.evaluators import judge
from overbae.services.eval.evaluators.base import JudgeResult
from overbae.services.eval.rubric_compiler import build_judge_prompt

logger = logging.getLogger(__name__)

MIN_ITERATION_IMPROVEMENT = 1.0


def _normalise_model_id(value) -> str:
    """Normalize OpenRouter's optional namespace without dropping provider identity."""
    return str(value or "").strip().lower().removeprefix("openrouter/")


def _model_ids_match(expected: str, observed: str) -> bool:
    expected = _normalise_model_id(expected)
    observed = _normalise_model_id(observed)
    return expected == observed or (
        "/" in expected and "/" not in observed and expected.rsplit("/", 1)[-1] == observed
    )


def _telemetry_values(payload, names: tuple[str, ...]) -> list[str]:
    """Collect scalar telemetry values from a nested result payload, in walk order."""
    values: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_norm = str(key).lower().replace("-", "_").replace(".", "_")
            if key_norm in names and isinstance(value, (str, int, float)) and str(value).strip():
                values.append(str(value).strip())
            else:
                values.extend(_telemetry_values(value, names))
    elif isinstance(payload, (list, tuple)):
        for value in payload:
            values.extend(_telemetry_values(value, names))
    return values


def _telemetry_value(payload, names: tuple[str, ...]) -> str:
    """The first scalar telemetry value in a nested result payload, or ``""``."""
    return next(iter(_telemetry_values(payload, names)), "")


def optimizer_dataset_error(capability, dataset, cell=None) -> str | None:
    """Why ``dataset`` can't drive an optimizer run for ``capability`` — None if it can.

    The gate for both create doors. It checks the version's contracts, not the
    entrypoint signature; a wrong signature still surfaces at the smoke test.
    """
    from overbae.services.datasets import use  # noqa: PLC0415 — avoid import cycle
    from overbae.services.datasets.lifecycle import DatasetError  # noqa: PLC0415

    if dataset is None:
        return "No dataset given."
    if dataset.project_id != capability.project_id:
        return "This dataset belongs to a different project."
    try:
        use.check(dataset, "eval", cell=cell)
    except DatasetError as exc:
        return exc.detail
    return None


def _command_trajectory(command_input, output: str) -> tuple[dict, dict]:
    """Build a canonical (trajectory, structured) pair from one command run.

    The input becomes the user turns and the produced output the graded assistant
    turn; assistant turns embedded in the input are dropped so a reference answer
    never leaks in as the graded output. Goes through the eval normalizer so the
    shape matches what the pipeline reconstructs from a real trace.
    """
    from overbae.services.eval import chatml, normalizer  # noqa: PLC0415 — avoid import cycle

    messages = chatml.parse_messages(command_input)
    if not messages:
        if isinstance(command_input, str):
            content = command_input.strip()
        else:
            content = json.dumps(command_input, default=str) if command_input is not None else ""
        messages = [{"role": "user", "content": content}]
    messages = [m for m in messages if m.get("role") != "assistant"]
    out_str = (
        output if isinstance(output, str) else json.dumps(output, default=str, ensure_ascii=False)
    )
    messages.append({"role": "assistant", "content": out_str})

    normalized = normalizer.normalize_messages(messages)
    structured = normalizer.structure_trajectory(normalized)
    return normalized, structured


def _variant_score_from_summary(summary: dict, variant_id: str) -> float:
    """Collapse an eval run summary into one 0..100 candidate score.

    Uses the same ``overall_aggregate`` headline as finetuning evals so
    gate-only metrics stay out of the mean. ``variant_id`` is kept for callers
    that already pass it; optimizer runs carry a single variant.
    """
    from overbae.services.eval.comparison import overall_aggregate  # noqa: PLC0415

    payload = dict(summary or {})
    if not payload.get("metrics"):
        names: set[str] = set()
        for variant in (payload.get("variants") or {}).values():
            names.update((variant or {}).get("metrics") or {})
        payload["metrics"] = sorted(names)
    mean = overall_aggregate(payload).mean
    return (mean * 100) if mean is not None else 0.0


def _usable_output(command) -> str:
    """A command's gradeable output, or ``""`` when it failed or produced nothing.

    Callers keep these out of judge calls and out of the score mean: an execution
    error is not a model score, and one timeout must not become a zero.
    """
    if command.status == OptimizerCommand.Status.FAILED:
        return ""
    result = command.result if isinstance(command.result, dict) else {}
    output = result.get("output") or command.output or ""
    if not isinstance(output, str):
        output = json.dumps(output, default=str, ensure_ascii=False)
    return "" if output.strip() in {"", "-"} else output


def _coverage(commands_by_index: dict, total_datapoints: int) -> dict:
    """Which datapoints produced gradeable output, and why others did not."""
    errors: list[str] = []
    scored = 0
    for index in range(total_datapoints):
        command = commands_by_index.get(index)
        if command is None:
            errors.append(f"datapoint {index}: no command output")
        elif _usable_output(command):
            scored += 1
        elif command.status == OptimizerCommand.Status.FAILED:
            errors.append(command.error[:500] or f"datapoint {index}: execution failed")
        else:
            errors.append(f"datapoint {index}: empty output")
    total_rows = total_datapoints
    return {
        "excluded_commands": total_rows - scored,
        "errors": errors[:10],
        "scored_rows": scored,
        "graded_rows": total_rows,
        "total_rows": total_rows,
        "coverage_rate": scored / total_rows if total_rows else 0.0,
    }


def candidate_comparison_rank(candidate) -> tuple[float, float, int]:
    """Lexicographic rank for model-comparison winner selection."""
    scores = candidate.scores or {}
    coverage = scores.get("coverage") or {}
    coverage_rate = float(
        scores.get("coverage_rate")
        if scores.get("coverage_rate") is not None
        else coverage.get("coverage_rate") or 0.0
    )
    excluded = int(coverage.get("excluded_commands") or 0)
    return (candidate.score, coverage_rate, -excluded)


class OptimizerExperiment(models.Model):
    """Orchestrates the optimization run; ``status`` is a coarse rollup of children."""

    class Mode(models.TextChoices):
        OPTIMIZE = "optimize"
        MODEL_COMPARISON = "model_comparison"
        HYBRID = "hybrid", "Harness + Models"

    class Status(models.TextChoices):
        SCHEDULED = "scheduled"
        BASELINE = "baseline"
        # Wait-states: the FSM parks in EVALUATING_* while an iteration's EvalRun
        # grades on the worker, and the eval-complete callback moves it to
        # EVALUATED_*. Reached only when the capability has runnable evaluators;
        # otherwise BASELINE/ITERATING score inline.
        EVALUATING_BASELINE_OUTPUTS = "evaluating_baseline_outputs"
        EVALUATED_BASELINE_OUTPUTS = "evaluated_baseline_outputs"
        ITERATING = "iterating"
        EVALUATING_CANDIDATE_OUTPUTS = "evaluating_candidate_outputs"
        EVALUATED_CANDIDATE_OUTPUTS = "evaluated_candidate_outputs"
        COMPLETED = "completed"
        FAILED = "failed"
        CANCELLED = "cancelled"
        PAUSED = "paused"

    class OpenRouterKeySource(models.TextChoices):
        PLATFORM = "platform", "Overmind credits"
        LOCAL = "local", "Local .env"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        "overbae.Project", on_delete=models.CASCADE, related_name="optimizer_experiments"
    )
    capability = models.ForeignKey(
        "overbae.Capability", on_delete=models.CASCADE, related_name="optimizer_experiments"
    )
    dataset = models.ForeignKey(
        "overbae.Dataset",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimizer_experiments",
    )
    # The cell whose rows ``datapoint_index`` counts into.
    cell = models.ForeignKey(
        "overbae.Cell",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="optimizer_experiments",
    )
    # Defaults to the capability's ``active_eval_set`` at create time and is pinned
    # here, so activating a different set cannot rewrite an in-flight
    # experiment's graders.
    eval_set = models.ForeignKey(
        "overbae.EvalSet",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimizer_experiments",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    entrypoint = models.CharField(max_length=512, blank=True, default="")
    code_trigger = models.TextField(blank=True, default="")
    mode = models.CharField(max_length=32, choices=Mode.choices, default=Mode.OPTIMIZE)
    model_ids = models.JSONField(default=list, blank=True)
    # Model-access trust boundary: ``platform`` routes completions through
    # Overmind's gateway on the client's ``OVERMIND_API_KEY``; ``local``
    # leaves the OpenRouter key in the client's own process env.
    openrouter_key_source = models.CharField(
        max_length=16,
        choices=OpenRouterKeySource.choices,
        default=OpenRouterKeySource.PLATFORM,
    )

    status = models.CharField(max_length=40, choices=Status.choices, default=Status.SCHEDULED)
    current_iteration = models.IntegerField(default=0)
    num_iterations = models.IntegerField(default=5)
    num_candidates_per_iteration = models.IntegerField(default=3)
    max_iterations_without_improvement = models.IntegerField(default=3)
    scores = models.JSONField(default=dict, blank=True)
    # Cursor SDK TokenUsage summed over smoke + candidate codegen.
    cursor_usage = models.JSONField(default=dict, blank=True)
    # Token-parameterised shell script, shared by every iteration so codegen runs
    # once per experiment.
    command_template = models.TextField(blank=True, default="")
    stalled_iterations = models.IntegerField(default=0)
    # winner_score, eval_pending, model_comparison, …
    state = models.JSONField(default=dict, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def generate_next(self):
        """Score posted outputs. The client owns codegen, smoke, and datapoint runs."""
        S = self.Status  # noqa: N806
        IC = OptimizerIteration.Status  # noqa: N806
        CC = OptimizerCommand.Status  # noqa: N806

        if self.status in (S.COMPLETED, S.FAILED, S.CANCELLED, S.PAUSED):
            return

        if self.status in (
            S.SCHEDULED,
            S.EVALUATED_BASELINE_OUTPUTS,
            S.EVALUATED_CANDIDATE_OUTPUTS,
        ):
            return

        if self.status in (S.BASELINE, S.ITERATING):
            order = 0 if self.status == S.BASELINE else self.current_iteration + 1
            iteration = self.iterations.filter(order=order).first()
            if iteration is None:
                return
            if iteration.status == IC.FAILED:
                self._fail(
                    "baseline iteration failed"
                    if order == 0
                    else f"iteration {iteration.order} failed"
                )
                return
            pending_cmds = self.commands.filter(
                iteration=iteration, status__in=[CC.PENDING, CC.RUNNING]
            )
            if pending_cmds.exists():
                return
            if iteration.status not in (IC.EVALUATED, IC.COMPLETED):
                if self._has_runnable_evaluators():
                    self.status = (
                        S.EVALUATING_BASELINE_OUTPUTS
                        if order == 0
                        else S.EVALUATING_CANDIDATE_OUTPUTS
                    )
                    self.save(update_fields=["status", "updated_at"])
                    return
                iteration.evaluate()
                self._record_eval_scores(iteration)
                if order > 0:
                    self.current_iteration = max(self.current_iteration, order)
                self.status = (
                    S.EVALUATED_BASELINE_OUTPUTS if order == 0 else S.EVALUATED_CANDIDATE_OUTPUTS
                )
                self.save()
                return
            self.status = (
                S.EVALUATED_BASELINE_OUTPUTS if order == 0 else S.EVALUATED_CANDIDATE_OUTPUTS
            )
            self.save()
            return

        if self.status == S.EVALUATING_BASELINE_OUTPUTS:
            if self._iteration_eval_pending(0):
                return
            if self.start_iteration_eval(0):
                return
            baseline = self.iterations.filter(order=0).first()
            if baseline is not None:
                self._record_eval_scores(baseline)
            self.status = S.EVALUATED_BASELINE_OUTPUTS
            self.save()
            return

        if self.status == S.EVALUATING_CANDIDATE_OUTPUTS:
            # The ledger bumps current_iteration to the order under evaluation before
            # the evals finish, so current_iteration+1 points past it — derive the
            # order from the newest iteration row instead.
            active = self.iterations.exclude(order=0).order_by("-order").first()
            if active is None:
                return
            order = active.order
            if self._iteration_eval_pending(order):
                return
            if self.start_iteration_eval(order):
                return
            self._record_eval_scores(active)
            self.status = S.EVALUATED_CANDIDATE_OUTPUTS
            self.save()
            return

    def advance(self):
        """Drive scoring wait-states until parked on the client or done."""
        for _ in range(10_000):
            before = (self.status, self.current_iteration)
            self.generate_next()
            if (self.status, self.current_iteration) == before:
                return

    def _stop_children(self, *, reason: str) -> None:
        """Fail in-flight commands/candidates/iterations so nothing keeps running them."""
        OptimizerCommand.objects.filter(
            experiment=self,
            status__in=[OptimizerCommand.Status.PENDING, OptimizerCommand.Status.RUNNING],
        ).update(status=OptimizerCommand.Status.FAILED, error=reason)

        OptimizerCandidate.objects.filter(
            experiment=self,
            status__in=[
                OptimizerCandidate.Status.PENDING,
                OptimizerCandidate.Status.RUNNING_COMMANDS,
                OptimizerCandidate.Status.COMMANDS_DONE,
                OptimizerCandidate.Status.EVALUATING,
            ],
        ).update(status=OptimizerCandidate.Status.FAILED)

        OptimizerIteration.objects.filter(
            experiment=self,
            status__in=[
                OptimizerIteration.Status.PENDING,
                OptimizerIteration.Status.GENERATING_CANDIDATES,
                OptimizerIteration.Status.GENERATED_CANDIDATES,
                OptimizerIteration.Status.RUNNING_COMMANDS,
                OptimizerIteration.Status.EVALUATING,
            ],
        ).update(status=OptimizerIteration.Status.FAILED)

    def _fail(self, reason: str) -> None:
        """Park the experiment in FAILED and stop every in-flight child."""
        with transaction.atomic():
            self._stop_children(reason=reason)
            self.failure_reason = (reason or "").strip()[:2000]
            self.status = self.Status.FAILED
            self.save(update_fields=["failure_reason", "status", "updated_at"])
        transaction.on_commit(self._charge_cursor_usage)

    def cancel(self):
        if self.status in (self.Status.COMPLETED, self.Status.FAILED, self.Status.CANCELLED):
            return

        with transaction.atomic():
            self._stop_children(reason="experiment cancelled")
            self.status = self.Status.CANCELLED
            self.save(update_fields=["status", "updated_at"])

    def _charge_cursor_usage(self) -> None:
        """Debit accumulated Cursor SDK usage for this experiment. Never raises."""
        from overbae.core.model_registry import WORKSHOP_ENGINES
        from overbae.models import BillingService
        from overbae.services.billing_ledger import charge_llm_usage

        if not self.triggered_by_id or not self.cursor_usage:
            return
        usage = self.cursor_usage
        cached = int(usage.get("cache_read_tokens") or 0)
        charge_llm_usage(
            self.triggered_by,
            {
                "prompt_tokens": int(usage.get("input_tokens") or 0) + cached,
                "completion_tokens": int(usage.get("output_tokens") or 0),
                "cached_tokens": cached,
                "served_model": WORKSHOP_ENGINES[0].model,
            },
            service=BillingService.CURSOR_AGENT,
            project_id=self.project_id,
            idempotency_key=f"cursor-agent:optimizer:{self.pk}",
            metadata={"source": "optimizer", "experiment_id": str(self.pk)},
        )

    def _record_baseline_scores(self, iteration: OptimizerIteration):
        best = max((c.score for c in iteration.candidates.all()), default=0.0)
        self.scores["baseline"] = best
        self.scores["best"] = best

    def _record_eval_scores(self, iteration: OptimizerIteration) -> None:
        """Model comparison never folds order=0 as an incumbent baseline."""
        if self.mode == self.Mode.MODEL_COMPARISON or iteration.order != 0:
            self._record_iteration_scores(iteration)
        else:
            self._record_baseline_scores(iteration)

    def _incumbent_model_name(self) -> str:
        """Stable display identity for the model used by the unchanged baseline."""
        active = getattr(self.capability, "active_model", None)
        return (
            (getattr(active, "model_id", "") if active is not None else "")
            or getattr(self.capability, "model", "")
            or "Incumbent model"
        )

    def _harness_index(self, candidate: OptimizerCandidate) -> int:
        """1-based harness round of a hybrid candidate; models fan out within a round."""
        return candidate.candidate_index // max(len(self.model_ids), 1) + 1

    def _candidate_label(self, candidate: OptimizerCandidate) -> tuple[str, str]:
        """The eval variant's (human label, model card identity)."""
        model_name = candidate.target_model or self._incumbent_model_name()
        if candidate.is_baseline:
            return "Baseline", model_name
        if self.mode == self.Mode.MODEL_COMPARISON:
            return model_name, model_name
        if self.mode == self.Mode.HYBRID:
            return f"Candidate {self._harness_index(candidate)} · {model_name}", model_name
        return f"Candidate {candidate.candidate_index + 1}", model_name

    def _runnable_evaluators(self) -> list:
        """Graders for this experiment: pinned eval_set, else capability active set."""
        from overbae.services.eval.eval_set import (  # noqa: PLC0415 — avoid import cycle
            resolve_members,
            runnable_capability_evaluators,
        )

        if self.eval_set_id is not None:
            members = resolve_members(self.eval_set)
            if members:
                return members
        return runnable_capability_evaluators(self.capability)

    def _has_runnable_evaluators(self) -> bool:
        return bool(self._runnable_evaluators())

    def start_iteration_eval(self, iteration_order: int) -> bool:
        """Kick off grading for every candidate in one iteration, one
        :class:`EvalRun` each so they list separately on the evals page.

        ``True`` means at least one run was dispatched and the caller must park in
        an ``EVALUATING_*`` state; ``False`` means there were no runnable
        evaluators and the stub already scored every candidate synchronously.
        """
        from overbae.tasks.eval import dispatch_preseeded_eval_run  # noqa: PLC0415 — avoid cycle

        iteration = OptimizerIteration.objects.get(experiment=self, order=iteration_order)
        candidates = list(iteration.candidates.all())

        pending = dict(self.state.get("eval_pending", {}))
        to_dispatch: list[tuple] = []

        for candidate in candidates:
            run = self.build_candidate_eval_run(candidate)
            if run is None:
                continue
            pending[str(candidate.id)] = str(run.id)
            to_dispatch.append((candidate, run))

        if not to_dispatch:
            self._evaluate_commands_stub(iteration)
            return False

        # Persist the pending markers BEFORE dispatch so eager Celery callbacks
        # can clear them; saving after dispatch re-stamps already-finished
        # candidates as pending.
        self.state["eval_pending"] = pending
        self.save(update_fields=["state", "updated_at"])

        for candidate, run in to_dispatch:
            dispatch_preseeded_eval_run(
                run,
                on_complete=optimizer_on_candidate_eval_complete.s(
                    experiment_id=str(self.id),
                    candidate_id=str(candidate.id),
                    iteration_order=iteration_order,
                ),
            )
        return True

    def _iteration_eval_pending(self, iteration_order: int) -> bool:
        iteration = OptimizerIteration.objects.filter(
            experiment=self, order=iteration_order
        ).first()
        if iteration is None:
            return False
        pending = self.state.get("eval_pending") or {}
        return any(str(c.id) in pending for c in iteration.candidates.all())

    def on_iteration_eval_complete(self, iteration_order: int) -> None:
        """Finalise iteration scores once every candidate eval run has finished.

        Called by :func:`optimizer_on_candidate_eval_complete` after the last
        candidate clears its pending marker.
        """
        iteration = OptimizerIteration.objects.get(experiment=self, order=iteration_order)
        self._record_eval_scores(iteration)
        if iteration_order > 0:
            self.current_iteration = max(self.current_iteration, iteration_order)
        self.status = (
            self.Status.EVALUATED_BASELINE_OUTPUTS
            if iteration_order == 0
            else self.Status.EVALUATED_CANDIDATE_OUTPUTS
        )
        self.save()

    def build_candidate_eval_run(self, candidate: OptimizerCandidate):
        """Create one :class:`EvalRun` for a candidate and pre-seed its samples:
        one variant, one ``RunEvaluator`` per runnable evaluator, and one sample
        per datapoint carrying the candidate's produced output. Returns ``None``
        when there are no runnable evaluators or no data, and the caller then
        falls back to the stub.
        """
        from overbae.models import (  # noqa: PLC0415 — avoid model import cycle
            EvalRun,
            EvalSample,
            EvalVariant,
            RunEvaluator,
        )
        from overbae.services.datasets import rows as row_store  # noqa: PLC0415
        from overbae.services.eval import snapshots  # noqa: PLC0415 — avoid import cycle
        from overbae.services.eval.eval_set import (  # noqa: PLC0415 — avoid import cycle
            expand_to_run_evaluators,
            resolve_members,
        )

        evaluators = self._runnable_evaluators()
        if not evaluators or self.cell_id is None:
            return None

        datapoints = list(row_store.iter_rows(self.cell))
        if not datapoints:
            return None

        commands = {cmd.datapoint_index: cmd for cmd in candidate.commands.all()}

        scored_datapoints = []
        for d_index, datapoint in enumerate(datapoints):
            command = commands.get(d_index)
            output = ""
            if command is not None:
                result = command.result or {}
                if command.status != OptimizerCommand.Status.FAILED:
                    telemetry_error = command._telemetry_error(result)
                    if telemetry_error:
                        command.status = OptimizerCommand.Status.FAILED
                        command.error = telemetry_error
                        command.result = result
                        command.save(update_fields=["status", "error", "result", "updated_at"])
                    elif result.get("telemetry_missing") or result.get("telemetry"):
                        command.result = result
                        command.save(update_fields=["result", "updated_at"])
                output = _usable_output(command)
            scored_datapoints.append((d_index, datapoint, command, output))

        coverage = _coverage(commands, len(datapoints))

        # Expand from a real EvalSet when it has generative members, which is what
        # carries prompt scoping; flat snapshotting is the no-set fallback.
        source_set = None
        if self.eval_set_id is not None and resolve_members(self.eval_set):
            source_set = self.eval_set
        elif self.eval_set_id is None:
            active = getattr(self.capability, "active_eval_set", None)
            if active is not None and resolve_members(active):
                source_set = active

        label, model_name = self._candidate_label(candidate)
        run_label = label if label == model_name else f"{label} · {model_name}"
        with transaction.atomic():
            run = EvalRun.objects.create(
                project=self.project,
                name=f"Optimizer · {self.capability.name} · {run_label}"[:255],
                description=f"Optimizer experiment {self.id}, {run_label}",
                data_source=EvalRun.DataSource.DATASET,
                dataset=self.dataset,
                cell=self.cell,
                eval_set=source_set,
                max_items=len(datapoints),
                triggered_by=self.triggered_by,
                status=EvalRun.Status.PENDING,
            )
            if source_set is not None:
                expand_to_run_evaluators(run, source_set)
            else:
                for order, evaluator in enumerate(evaluators):
                    RunEvaluator.objects.create(
                        run=run,
                        evaluator=evaluator,
                        snapshot=snapshots.build_snapshot(evaluator),
                        order=order,
                    )
            variant = EvalVariant.objects.create(
                run=run,
                label=label,
                mode=EvalVariant.Mode.EXISTING,
                is_baseline=candidate.is_baseline,
                order=0,
                model_name=model_name,
                params={
                    "optimizer_candidate_id": str(candidate.id),
                    "optimizer_mode": self.mode,
                    "target_model": candidate.target_model or model_name,
                },
            )
            for _d_index, datapoint, command, output in scored_datapoints:
                result = (command.result or {}) if command is not None else {}
                trace_id = result.get("trace_id", "")
                trajectory, structured = _command_trajectory(datapoint.input, output)
                EvalSample.objects.create(
                    run=run,
                    variant=variant,
                    row_index=datapoint.index,
                    source_trace_id=trace_id,
                    trajectory=trajectory,
                    structured=structured,
                    expected=datapoint.expected_output,
                )
            candidate.eval_run = run
            candidate.scores = {
                **(candidate.scores or {}),
                "coverage": coverage,
                "graded_rows": coverage["graded_rows"],
                "total_rows": coverage["total_rows"],
                "coverage_rate": coverage["coverage_rate"],
            }
            candidate.save(update_fields=["eval_run", "scores"])
        return run

    def _fold_candidate_eval_scores(self, candidate: OptimizerCandidate) -> float:
        """Fold a finished eval run's scores into its candidate. A candidate owns
        exactly one run with one variant, so the variant is read straight off it.
        """
        run = candidate.eval_run
        if run is None:
            return 0.0
        run.refresh_from_db()
        variant = run.variants.first()
        score = (
            _variant_score_from_summary(run.summary, str(variant.id))
            if variant is not None
            else 0.0
        )
        coverage = (candidate.scores or {}).get("coverage") or {}
        measurement = (run.summary or {}).get("measurement") or {}
        candidate.score = score
        candidate.scores = {
            **(candidate.scores or {}),
            "coverage": coverage,
            "graded_rows": coverage.get("graded_rows"),
            "total_rows": coverage.get("total_rows"),
            "coverage_rate": coverage.get("coverage_rate"),
            "measurement": measurement,
        }
        candidate.status = OptimizerCandidate.Status.EVALUATED
        candidate.save(update_fields=["score", "scores", "status"])
        if variant is not None:
            self._fold_command_scores(candidate, variant, run)
        return score

    def _fold_command_scores(self, candidate, variant, run) -> None:
        """Mirror each sample's mean score onto its command: grading happens at
        sample level, and the experiment UI still reads per-command scores.
        """
        from overbae.models import EvalSample, Score  # noqa: PLC0415 — avoid cycle

        samples = {s.row_index: s for s in EvalSample.objects.filter(run=run, variant=variant)}
        for command in candidate.commands.all():
            sample = samples.get(command.datapoint_index)
            if sample is None:
                continue
            rows = Score.objects.filter(sample=sample, outcome=Score.Outcome.SCORED).exclude(
                name__endswith="__prediction"
            )
            values = [row.value * 100 for row in rows if row.value is not None]
            command.score = sum(values) / len(values) if values else 0.0
            command.status = OptimizerCommand.Status.EVALUATED
            command.save(update_fields=["score", "status"])

    def _evaluate_commands_stub(self, iteration: OptimizerIteration) -> None:
        """Fallback scorer for capabilities with no runnable evaluators, so the loop
        still makes progress before evaluators are set up.
        """
        for candidate in iteration.candidates.all():
            candidate.evaluate()

    def _record_iteration_scores(self, iteration: OptimizerIteration):
        if self.mode == self.Mode.MODEL_COMPARISON:
            self._record_model_comparison_scores(iteration)
            return

        candidate_scores = {str(c.id): c.score for c in iteration.candidates.all()}
        iteration_best = max(candidate_scores.values(), default=0.0)
        self.scores[str(iteration.order)] = candidate_scores
        previous_best = self.scores.get("best", 0.0)
        # ``best`` is ranking truth, so record every higher score. The 1-point
        # threshold gates plateau detection only; ranking with it could crown 97.2
        # over a later 97.5.
        if iteration_best > previous_best:
            self.scores["best"] = iteration_best
        if iteration_best > previous_best + MIN_ITERATION_IMPROVEMENT:
            self.stalled_iterations = 0
        else:
            self.stalled_iterations += 1
        iteration.status = OptimizerIteration.Status.COMPLETED
        iteration.save(update_fields=["status"])

    def _record_model_comparison_scores(self, iteration: OptimizerIteration) -> None:
        models = {c.target_model: c.score for c in iteration.candidates.all() if c.target_model}
        iteration_best = max(models.values(), default=0.0)
        scores = dict(self.scores or {})
        scores["best"] = max(scores.get("best", 0.0) or 0.0, iteration_best)
        by_model = dict(scores.get("by_model") or scores.get("models") or {})
        by_model.update(models)
        scores["by_model"] = by_model
        scores["models"] = by_model
        scores[str(iteration.order)] = {str(c.id): c.score for c in iteration.candidates.all()}
        self.scores = scores
        iteration.scores = {"best": iteration_best, "models": models}
        iteration.status = OptimizerIteration.Status.COMPLETED
        iteration.save(update_fields=["status", "scores"])

    def should_continue_iterating(self) -> bool:
        if self.mode == self.Mode.MODEL_COMPARISON:
            # Comparison runs every selected model regardless of stall: the run
            # owes a full per-model table, not improvement-driven stopping.
            return self.current_iteration < self.num_iterations
        if self.current_iteration >= self.num_iterations:
            return False
        return self.stalled_iterations < self.max_iterations_without_improvement

    def best_candidate(self) -> OptimizerCandidate | None:
        """The highest-scoring non-baseline candidate across all iterations, tied
        on highest iteration order then lowest candidate_index. None when nothing
        scored above the baseline.
        """
        baseline = self.scores.get("baseline", 0.0)
        return (
            self.candidates.filter(is_baseline=False)
            .exclude(code_path="")
            .order_by("-score", "-iteration__order", "candidate_index")
            .select_related("iteration")
            .filter(score__gt=baseline)
            .first()
        )

    def generate_winner(self):
        """Record the winning score and candidate id so the downstream PR step can read them."""
        if self.mode == self.Mode.MODEL_COMPARISON:
            # ``_record_model_comparison_scores`` already maxed ``best`` over
            # these same rows when the iteration was scored.
            candidates = list(self.candidates.filter(is_baseline=False).exclude(target_model=""))
            uncovered: set[str] = set()
            for row in candidates:
                measurement = (row.scores or {}).get("measurement") or {}
                uncovered.update(measurement.get("uncovered_card_claims") or [])
            suite_incomplete = bool(uncovered)

            ranked = sorted(candidates, key=candidate_comparison_rank, reverse=True)
            selected_winner = ranked[0] if ranked else None
            if suite_incomplete and len(ranked) > 1 and selected_winner is not None:
                top_rank = candidate_comparison_rank(selected_winner)
                if all(candidate_comparison_rank(row) == top_rank for row in ranked):
                    selected_winner = None

            selected_score = selected_winner.score if selected_winner is not None else 0.0
            incumbent_score = self.scores.get("baseline", 0.0)
            incumbent_wins = incumbent_score >= selected_score
            comparison_state = {
                "selected_winner": selected_winner.target_model if selected_winner else "",
                "selected_winner_score": selected_score,
                "incumbent_score": incumbent_score,
                "overall_winner": (
                    "incumbent"
                    if incumbent_wins or selected_winner is None
                    else selected_winner.target_model
                ),
                "incumbent_wins": incumbent_wins,
            }
            if suite_incomplete:
                comparison_state["suite_incomplete"] = True
                comparison_state["uncovered_card_claims"] = sorted(uncovered)
                if selected_winner is None:
                    comparison_state["winner_note"] = (
                        "All candidates tied; suite has uncovered card claims."
                    )
            self.state["model_comparison"] = comparison_state
            return

        if self.mode == self.Mode.HYBRID:
            selected = (
                self.candidates.filter(is_baseline=False)
                .exclude(target_model="")
                .order_by("-score", "-iteration__order", "candidate_index")
                .select_related("iteration")
                .first()
            )
            incumbent_score = self.scores.get("baseline", 0.0)
            self.state["model_optimization"] = {
                "selected_model": selected.target_model if selected else "",
                "selected_harness_candidate": (self._harness_index(selected) if selected else None),
                "selected_score": selected.score if selected else 0.0,
                "incumbent_score": incumbent_score,
                "overall_winner": (
                    "incumbent"
                    if selected is None or incumbent_score >= selected.score
                    else f"{selected.target_model} · harness {self._harness_index(selected)}"
                ),
            }

        winner = self.best_candidate()
        recorded_best = self.scores.get("best", self.scores.get("baseline", 0.0))
        winner_score = max(recorded_best, winner.score if winner is not None else 0.0)
        self.scores["best"] = winner_score
        self.state["winner_score"] = winner_score
        if winner is not None:
            self.state["winner_candidate_id"] = str(winner.id)


class OptimizerIteration(models.Model):
    """One optimization round.  order=0 is the baseline; 1, 2, … are candidate rounds."""

    class Status(models.TextChoices):
        PENDING = "pending"
        GENERATING_CANDIDATES = "generating_candidates"
        GENERATED_CANDIDATES = "generated_candidates"
        RUNNING_COMMANDS = "running_commands"
        EVALUATING = "evaluating"
        EVALUATED = "evaluated"
        COMPLETED = "completed"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "overbae.OptimizerExperiment", on_delete=models.CASCADE, related_name="iterations"
    )
    order = models.IntegerField(default=0)
    name = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    scores = models.JSONField(default=dict, blank=True)
    patch = models.TextField(blank=True, default="")
    # Null when the capability had no runnable evaluators and the stub scored instead.
    eval_run = models.ForeignKey(
        "overbae.EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimizer_iterations",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(fields=["experiment", "order"], name="uniq_experiment_order")
        ]

    def recompute_status(self):
        """Update this iteration's status from its candidates' aggregate state."""
        candidates = list(self.candidates.all())
        if not candidates:
            return

        CS = OptimizerCandidate.Status  # noqa: N806
        statuses = {c.status for c in candidates}

        if all(c.status == CS.EVALUATED for c in candidates):
            new = self.Status.EVALUATED
        elif statuses & {CS.RUNNING_COMMANDS, CS.PENDING}:
            new = self.Status.RUNNING_COMMANDS
        elif all(c.status in (CS.COMMANDS_DONE, CS.EVALUATED, CS.FAILED) for c in candidates):
            # All-failed still rolls up to EVALUATING so evaluate() scores ~0
            # rather than hard-failing the batch. Only experiment._fail /
            # _stop_children set FAILED here.
            new = self.Status.EVALUATING
        else:
            return

        if new != self.status:
            self.status = new
            self.save(update_fields=["status"])

    def evaluate(self):
        """Score every candidate in this iteration, in parallel."""
        self.status = self.Status.EVALUATING
        self.save(update_fields=["status"])
        candidates = list(self.candidates.all())

        with ThreadPoolExecutor(max_workers=min(20, len(candidates))) as executor:
            futures = {executor.submit(c.evaluate): c for c in candidates}
            for future in as_completed(futures):
                future.result()

        best = max((c.score for c in self.candidates.all()), default=0.0)
        self.scores = {"best": best}
        self.status = self.Status.EVALUATED
        self.save(update_fields=["status", "scores"])


class OptimizerCandidate(models.Model):
    """One code variant (git diff) evaluated across all dataset datapoints."""

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING_COMMANDS = "running_commands"
        COMMANDS_DONE = "commands_done"
        EVALUATING = "evaluating"
        EVALUATED = "evaluated"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment = models.ForeignKey(
        "overbae.OptimizerExperiment", on_delete=models.CASCADE, related_name="candidates"
    )
    iteration = models.ForeignKey(
        "overbae.OptimizerIteration", on_delete=models.CASCADE, related_name="candidates"
    )
    candidate_index = models.IntegerField(default=0)
    code_path = models.TextField(blank=True, default="")
    target_model = models.CharField(max_length=255, blank=True, default="")
    is_baseline = models.BooleanField(default=False)
    score = models.FloatField(default=0.0)
    scores = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.PENDING)
    eval_run = models.ForeignKey(
        "overbae.EvalRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="optimizer_candidates",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["candidate_index"]

    def recompute_status(self):
        """Update this candidate's status from its commands' aggregate state."""
        commands = list(self.commands.all())
        if not commands:
            return

        CS = OptimizerCommand.Status  # noqa: N806
        statuses = {c.status for c in commands}

        if all(c.status == CS.EVALUATED for c in commands):
            new = self.Status.EVALUATED
        elif statuses & {CS.PENDING, CS.RUNNING}:
            new = self.Status.RUNNING_COMMANDS
        elif all(c.status in (CS.RAN, CS.FAILED, CS.EVALUATED) for c in commands):
            # FAILED folds in with RAN/EVALUATED so evaluate() still runs, usually
            # scoring ~0. Only experiment._fail / _stop_children set FAILED here.
            new = self.Status.COMMANDS_DONE
        else:
            return

        if new != self.status:
            self.status = new
            self.save(update_fields=["status"])

    def evaluate(self):
        """Score all commands in one batch and store the mean on ``self.score``.

        Judge calls run concurrently and scores are bulk-written, so the DB touch
        count stays constant regardless of how many commands there are.
        """
        from overbae.models.evaluation import Evaluator

        self.status = self.Status.EVALUATING
        self.save(update_fields=["status"])

        evaluator = Evaluator.objects.filter(
            project=self.experiment.project, capability=self.experiment.capability
        ).last()
        commands = list(self.commands.all())
        eligible = [command for command in commands if _usable_output(command)]
        pending = [c for c in eligible if c.status != OptimizerCommand.Status.EVALUATED]

        def _score(cmd):
            output = _usable_output(cmd)
            if evaluator is not None:
                prompt = build_judge_prompt(evaluator, {"input": cmd.input, "output": output})
                outcome = judging.invoke_judge(
                    prompt,
                    response_format=JudgeResult,
                    judge_model=evaluator.judge_model,
                    project_id=self.experiment.project_id,
                    system_prompt=judge.JUDGE_SYSTEM_PROMPT,
                )
                cmd.score = evaluator.normalize_score(getattr(outcome.parsed, "score", 0.0) or 0.0)
            else:
                cmd.score = 0.0 if output in (None, "") else randint(60, 85)
            cmd.status = OptimizerCommand.Status.EVALUATED

        if pending:
            with ThreadPoolExecutor(max_workers=min(20, len(pending))) as executor:
                for future in as_completed(executor.submit(_score, c) for c in pending):
                    future.result()
            OptimizerCommand.objects.bulk_update(pending, ["score", "status"])

        self.score = sum(c.score for c in eligible) / len(eligible) if eligible else 0.0
        self.scores = {
            **(self.scores or {}),
            "coverage": _coverage({c.datapoint_index: c for c in commands}, len(commands)),
        }
        self.status = self.Status.EVALUATED
        self.save(update_fields=["score", "scores", "status"])


class OptimizerCommand(models.Model):
    """One datapoint run of one candidate, executed locally by the SDK.

    The client renders the template, runs it, and posts the outcome through the
    ledger (``post_results``); the server only scores what lands here.
    """

    class Status(models.TextChoices):
        PENDING = "pending"
        RUNNING = "running"
        RAN = "ran"  # execution succeeded; not yet scored
        PASSED = "passed"  # legacy executioner alias for RAN; old rows only
        EVALUATED = "evaluated"
        FAILED = "failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    experiment: OptimizerExperiment = models.ForeignKey(
        "overbae.OptimizerExperiment", on_delete=models.CASCADE, related_name="commands"
    )
    candidate = models.ForeignKey(
        "overbae.OptimizerCandidate",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="commands",
    )
    iteration = models.ForeignKey(
        "overbae.OptimizerIteration",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="commands",
    )
    datapoint_index = models.IntegerField(default=0)
    # Copied from the dataset row so evaluators score against the actual input.
    input = models.JSONField(default=dict, blank=True)
    code_path = models.TextField(blank=True, default="")
    command = models.TextField(blank=True, default="")
    output = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)  # {output, trace_id, exit_code, ...}
    score = models.FloatField(default=0.0)
    trace_type = models.CharField(max_length=16, default="original")  # "original" or "replay"
    original_trace_id = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    timeout = models.IntegerField(default=600)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]

    def _trace_telemetry(self, trace_id: str) -> tuple[str, list[str]]:
        """Read provider/model observations from all LLM spans for a trace."""
        if not trace_id:
            return "", []
        from overbae.models.traces import Span  # noqa: PLC0415 — avoid import cycle

        providers: set[str] = set()
        models: set[str] = set()
        rows = Span.objects.filter(
            project_id=self.experiment.project_id, trace_id=trace_id
        ).values_list("attributes", "resource_attrs")
        for attributes, resource_attrs in rows.iterator():
            for attrs in (attributes, resource_attrs):
                provider = _telemetry_value(
                    attrs,
                    (
                        "provider",
                        "observed_provider",
                        "genai_provider",
                        "llm_provider",
                        "gen_ai_system",
                    ),
                )
                model = _telemetry_value(
                    attrs,
                    (
                        "model",
                        "genai_model",
                        "gen_ai_request_model",
                        "gen_ai_response_model",
                        "llm_model",
                    ),
                )
                if provider:
                    providers.add(provider)
                if model:
                    models.add(model)
        return ", ".join(sorted(providers)), sorted(models)

    def _telemetry_error(self, result: dict) -> str:
        """Return a clear routing mismatch, or ``""`` when it is unobservable."""
        if not isinstance(result, dict):
            return ""
        expected_model = (self.candidate.target_model if self.candidate_id else "") or ""
        if not expected_model:
            return ""

        provider_names = (
            "provider",
            "observed_provider",
            "genai_provider",
            "llm_provider",
            "gen_ai_system",
        )
        model_names = (
            "model",
            "observed_model",
            "genai_model",
            "gen_ai_request_model",
            "gen_ai_response_model",
            "llm_model",
        )
        # Only explicit telemetry containers and top-level fields: a structured
        # capability output may hold a business field called ``model``, which must not
        # be read as provider telemetry.
        telemetry_payload = result.get("telemetry") or result.get("observed_telemetry") or {}
        direct = {
            key: value
            for key, value in result.items()
            if str(key).lower().replace("-", "_").replace(".", "_")
            in {*provider_names, *model_names}
        }
        result_telemetry = [telemetry_payload, direct]
        result_providers = _telemetry_values(result_telemetry, provider_names)
        models = _telemetry_values(result_telemetry, model_names)
        provider = ", ".join(sorted(set(result_providers)))
        trace_provider, trace_models = self._trace_telemetry(str(result.get("trace_id") or ""))
        provider = provider or trace_provider
        models.extend(trace_models)

        if provider or models:
            telemetry = result.get("telemetry") if isinstance(result.get("telemetry"), dict) else {}
            if provider:
                telemetry["provider"] = provider
            if models:
                telemetry["models"] = sorted(set(models))
                telemetry["model"] = models[0] if len(set(models)) == 1 else ""
            result["telemetry"] = telemetry
        result["telemetry_missing"] = not provider or not models

        mismatched = sorted(
            {model for model in models if not _model_ids_match(expected_model, model)}
        )
        if mismatched:
            return (
                "Model routing mismatch: expected OpenRouter model "
                f"{expected_model!r}, observed {', '.join(mismatched)!r}."
            )
        return ""

    def evaluate(self) -> float:
        """Score this command's output on 0..100 and set ``status=EVALUATED``."""
        output = self.result.get("output") if isinstance(self.result, dict) else self.output

        # Lazy: evaluation models may import optimizer types.
        from overbae.models.evaluation import Evaluator

        evaluator = Evaluator.objects.filter(
            project=self.experiment.project, capability=self.experiment.capability
        ).last()
        if evaluator is not None:
            prompt = build_judge_prompt(evaluator, {"input": self.input, "output": output})
            outcome = judging.invoke_judge(
                prompt,
                response_format=JudgeResult,
                judge_model=evaluator.judge_model,
                project_id=self.experiment.project.id,
                system_prompt=judge.JUDGE_SYSTEM_PROMPT,
            )
            self.score = evaluator.normalize_score(getattr(outcome.parsed, "score", 0.0) or 0.0)
        else:
            self.score = 0.0 if output in (None, "") else randint(60, 85)

        self.status = self.Status.EVALUATED
        return self.score


@shared_task(name="overbae.models.optimizer.optimizer_on_candidate_eval_complete")
def optimizer_on_candidate_eval_complete(
    _aggregate_result=None,
    *,
    experiment_id: str,
    candidate_id: str,
    iteration_order: int,
) -> None:
    """Eval-chord callback, chained after ``aggregate_run`` for each candidate.

    Folds that candidate's scores in and clears its pending marker; the last
    candidate of an iteration also finalises the iteration and resumes the FSM.
    """
    experiment = OptimizerExperiment.objects.filter(id=experiment_id).first()
    if experiment is None:
        return

    candidate = OptimizerCandidate.objects.filter(id=candidate_id).first()
    if candidate is not None:
        experiment._fold_candidate_eval_scores(candidate)

    with transaction.atomic():
        experiment.refresh_from_db()
        pending = dict(experiment.state.get("eval_pending", {}))
        pending.pop(candidate_id, None)
        experiment.state["eval_pending"] = pending
        experiment.save(update_fields=["state", "updated_at"])

    if not experiment._iteration_eval_pending(iteration_order):
        experiment.on_iteration_eval_complete(iteration_order)
        run_experiment_advance.delay(experiment_id)


@shared_task(name="overbae.models.optimizer.run_experiment_advance")
def run_experiment_advance(experiment_id: str) -> None:
    """Drive one experiment's FSM to its next client-wait state in a worker."""
    from overbae.tasks.utils.task_lock import acquire_task_lock

    logger.info("optimizer advance start exp=%s", experiment_id)
    with acquire_task_lock(f"optimizer_advance_{experiment_id}") as acquired:
        if not acquired:
            logger.info(
                "optimizer advance skipped exp=%s (another worker holds the lock)", experiment_id
            )
            return
        experiment = OptimizerExperiment.objects.filter(id=experiment_id).first()
        if experiment is None:
            logger.warning("optimizer advance: experiment %s not found", experiment_id)
            return
        status_before = experiment.status
        iter_before = experiment.current_iteration
        logger.info(
            "optimizer advance start exp=%s status=%s iteration=%d",
            experiment_id,
            status_before,
            iter_before,
        )
        try:
            experiment.advance()
        except Exception as exc:  # noqa: BLE001 — persist the failure; never lose it
            logger.exception("optimizer experiment %s advance failed", experiment_id)
            experiment._fail(f"advance failed: {type(exc).__name__}: {exc}"[:500])
            return
        logger.info(
            "optimizer advance end exp=%s status=%s→%s iteration=%d→%d",
            experiment_id,
            status_before,
            experiment.status,
            iter_before,
            experiment.current_iteration,
        )


def optimize_capability(
    capability,
    *,
    dataset=None,
    eval_set=None,
    entrypoint: str = "",
    code_trigger: str = "",
    num_iterations: int = 5,
    num_candidates_per_iteration: int = 3,
    max_iterations_without_improvement: int = 3,
    last_state: dict = None,
    triggered_by=None,
    mode: str = OptimizerExperiment.Mode.OPTIMIZE,
    model_ids: list[str] | None = None,
    openrouter_key_source: str = OptimizerExperiment.OpenRouterKeySource.PLATFORM,
) -> OptimizerExperiment:
    from overbae.services.optimizer_create import create_optimizer_experiment

    return create_optimizer_experiment(
        user=triggered_by,
        capability=capability,
        dataset=dataset,
        eval_set=eval_set,
        entrypoint=entrypoint,
        code_trigger=code_trigger,
        num_iterations=num_iterations,
        num_candidates_per_iteration=num_candidates_per_iteration,
        max_iterations_without_improvement=max_iterations_without_improvement,
        initial_state=last_state,
        mode=mode,
        model_ids=model_ids,
        openrouter_key_source=openrouter_key_source,
    )
