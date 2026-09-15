"""Ledger write API — the server half of the client-side optimizer FSM.

The client owns codegen, smoke testing, and command execution. These functions
record what the client reports, drive the eval pipeline, and seal the experiment.
"""

from __future__ import annotations

import logging

from rest_framework.exceptions import ValidationError

from overbae.models.optimizer import (
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
)

logger = logging.getLogger(__name__)


def set_command_template(experiment: OptimizerExperiment, template: str) -> OptimizerExperiment:
    """Store the client-generated command template. Experiment stays SCHEDULED."""
    experiment.command_template = (template or "").strip()
    experiment.save(update_fields=["command_template", "updated_at"])
    return experiment


def create_iteration(
    experiment: OptimizerExperiment,
    *,
    order: int,
    name: str = "",
    candidates: list[dict],
) -> OptimizerIteration:
    """Create an OptimizerIteration + OptimizerCandidate rows from client data.

    order=0 → baseline; advances experiment to BASELINE.
    order>0 → candidate round; advances experiment to ITERATING.

    ``candidates`` items: {candidate_index, code_path/patch, target_model, is_baseline}
    OptimizerCommand rows are NOT created here — the client posts results directly.
    """
    if not candidates:
        raise ValidationError({"candidates": "At least one candidate is required."})
    if order < 0:
        raise ValidationError({"order": "Iteration order must be 0 or greater."})

    iteration = OptimizerIteration.objects.create(
        experiment=experiment,
        order=order,
        name=name or ("Baseline" if order == 0 else f"Iteration {order}"),
        status=OptimizerIteration.Status.RUNNING_COMMANDS,
    )

    for cand_data in candidates:
        target_model = str(cand_data.get("target_model") or "")
        # A model-bearing candidate is a challenger: comparison runs its first
        # model at order 0, and generate_winner drops baselines from the table.
        OptimizerCandidate.objects.create(
            experiment=experiment,
            iteration=iteration,
            candidate_index=int(cand_data.get("candidate_index", 0)),
            code_path=str(cand_data.get("code_path") or cand_data.get("patch") or ""),
            target_model=target_model,
            is_baseline=bool(cand_data.get("is_baseline", order == 0 and not target_model)),
            status=OptimizerCandidate.Status.RUNNING_COMMANDS,
        )

    if order == 0:
        experiment.status = OptimizerExperiment.Status.BASELINE
    else:
        experiment.status = OptimizerExperiment.Status.ITERATING
    experiment.save(update_fields=["status", "updated_at"])
    return iteration


def post_results(experiment: OptimizerExperiment, results: list[dict]) -> dict:
    """Upsert OptimizerCommand rows with client-reported execution outcomes.

    Each entry: {candidate_id, datapoint_index, success, output, trace_id?, error?, input?}
    ``input`` is copied from the dataset datapoint when not supplied.
    ``command`` is left blank — the client ran it locally.
    """
    if not isinstance(results, list):
        raise ValidationError({"results": "Expected a list of result objects."})

    from overbae.services.datasets import rows as row_store  # noqa: PLC0415 — avoid import cycle

    datapoints_by_index: dict[int, object] = {}
    if experiment.cell_id:
        for dp in row_store.iter_rows(experiment.cell):
            datapoints_by_index[dp.index] = dp

    upserted = 0
    for entry in results:
        candidate_id = str(entry.get("candidate_id") or "")
        datapoint_index = int(entry.get("datapoint_index", 0))
        success = bool(entry.get("success", True))
        output = str(entry.get("output") or "")
        error = str(entry.get("error") or "")
        trace_id = str(entry.get("trace_id") or "")
        input_data = entry.get("input")
        if input_data is None:
            dp = datapoints_by_index.get(datapoint_index)
            input_data = dp.input if dp is not None else {}

        try:
            candidate = OptimizerCandidate.objects.select_related("iteration").get(
                experiment=experiment, id=candidate_id
            )
        except OptimizerCandidate.DoesNotExist:
            raise ValidationError(
                {"candidate_id": f"Candidate {candidate_id!r} not found in this experiment."}
            ) from None

        result_payload: dict = {}
        if output:
            result_payload["output"] = output
        if trace_id:
            result_payload["trace_id"] = trace_id

        OptimizerCommand.objects.update_or_create(
            experiment=experiment,
            candidate=candidate,
            iteration=candidate.iteration,
            datapoint_index=datapoint_index,
            defaults={
                "input": input_data,
                "output": output,
                "result": result_payload,
                "error": error,
                "status": (
                    OptimizerCommand.Status.RAN if success else OptimizerCommand.Status.FAILED
                ),
                "command": "",
            },
        )
        upserted += 1

        candidate.recompute_status()
        candidate.iteration.recompute_status()

    return {"upserted": upserted}


def evaluate_iteration(experiment: OptimizerExperiment, order: int) -> OptimizerExperiment:
    """Trigger EvalRun grading for one iteration after the client has finished running commands.

    Verifies all commands for the iteration are in a terminal status (RAN/PASSED/EVALUATED/FAILED),
    then sets EVALUATING_* and calls start_iteration_eval. If no runnable evaluators exist the
    stub scores synchronously and EVALUATED_* is set immediately.
    """
    try:
        iteration = OptimizerIteration.objects.get(experiment=experiment, order=order)
    except OptimizerIteration.DoesNotExist:
        raise ValidationError(
            {"order": f"Iteration {order} not found for this experiment."}
        ) from None

    _terminal = {
        OptimizerCommand.Status.RAN,
        OptimizerCommand.Status.PASSED,
        OptimizerCommand.Status.EVALUATED,
        OptimizerCommand.Status.FAILED,
    }
    non_terminal = experiment.commands.filter(iteration=iteration).exclude(status__in=_terminal)
    if non_terminal.exists():
        raise ValidationError(
            {
                "detail": (
                    "Not all commands for this iteration are complete. "
                    "Wait for all commands to reach RAN, PASSED, EVALUATED, or FAILED."
                )
            }
        )

    eval_status = (
        OptimizerExperiment.Status.EVALUATING_BASELINE_OUTPUTS
        if order == 0
        else OptimizerExperiment.Status.EVALUATING_CANDIDATE_OUTPUTS
    )
    experiment.status = eval_status
    if order > 0:
        # Track how far the client has progressed.
        experiment.current_iteration = max(experiment.current_iteration, order)
    experiment.save(update_fields=["status", "current_iteration", "updated_at"])

    dispatched = experiment.start_iteration_eval(order)
    if not dispatched:
        # No runnable evaluators: _evaluate_commands_stub already ran inside start_iteration_eval.
        experiment._record_eval_scores(iteration)
        evaluated_status = (
            OptimizerExperiment.Status.EVALUATED_BASELINE_OUTPUTS
            if order == 0
            else OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
        )
        experiment.status = evaluated_status
        experiment.save()

    experiment.refresh_from_db()
    return experiment


def complete_experiment(experiment: OptimizerExperiment) -> OptimizerExperiment:
    """Record the winner and mark the experiment COMPLETED.

    Safe to call from any non-terminal status; use after the last iteration's
    evaluate_iteration resolves (or when the client decides to stop early).
    """
    if experiment.status in (
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
    ):
        raise ValidationError({"detail": f"Experiment is already terminal ({experiment.status})."})

    experiment.generate_winner()
    experiment.status = OptimizerExperiment.Status.COMPLETED
    experiment._stop_children(reason="experiment completed")
    experiment.save()
    experiment._charge_cursor_usage()
    experiment.refresh_from_db()
    return experiment
