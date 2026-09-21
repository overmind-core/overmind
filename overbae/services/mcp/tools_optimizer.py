"""Project-scoped user-facing optimizer MCP tools."""

from __future__ import annotations

import uuid
from collections.abc import Callable

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Prefetch, Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from overbae.api.credit_gate import PaymentRequired
from overbae.models import (
    Capability,
    Dataset,
    EvalSet,
    OptimizerCandidate,
    OptimizerExperiment,
    OptimizerIteration,
)
from overbae.models.optimizer import candidate_comparison_rank, optimizer_dataset_error
from overbae.services.datasets.contract import public_intent
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.optimizer import (
    CheckOptimizerReadinessInput,
    CheckOptimizerReadinessOutput,
    InspectOptimizerResultInput,
    InspectOptimizerResultOutput,
    OptimizerCandidateResult,
    OptimizerCreditReadiness,
    OptimizerDatasetReadiness,
    OptimizerEvalSetReadiness,
    OptimizerExecutionerState,
    OptimizerExperimentReference,
    OptimizerIterationResult,
    OptimizerJobReference,
    OptimizerModelReadiness,
    OptimizerNextAction,
    OptimizerPlanReadiness,
    OptimizerResultSummary,
    OptimizerWinner,
    StartOptimizerInput,
    StartOptimizerOutput,
)
from overbae.services.mcp.errors import MCPError, mcp_cell, mcp_cell_contract, mcp_dataset
from overbae.services.mcp.resources import resource_link, safe_json
from overbae.services.optimizer_create import (
    create_optimizer_experiment,
    validate_optimizer_models,
)
from overbae.services.optimizer_prereqs import optimizer_prerequisite_report
from overbae.services.plan_limits import PlanLimitExceeded

_PATCH_CAP = 4_000


def _cell_ref(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _resolve_capability(context: MCPContext, reference: str) -> Capability:
    query = Capability.objects.filter(
        project=context.project, status=Capability.Status.CURRENT
    ).select_related("active_eval_set")
    normalized = _uuid(reference)
    capability = query.filter(id=normalized).first() if normalized else None
    if capability is None:
        capability = query.filter(name__iexact=reference.strip()).first()
    if capability is None:
        capability = query.filter(slug__iexact=reference.strip()).first()
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _resolve_dataset(context: MCPContext, reference: str) -> Dataset:
    return mcp_dataset(context, reference)


def _resolve_eval_set(context: MCPContext, reference: str, *, capability: Capability) -> EvalSet:
    query = EvalSet.objects.filter(
        Q(capability=capability) | Q(capability__isnull=True), project=context.project
    ).select_related("capability")
    normalized = _uuid(reference)
    eval_set = query.filter(id=normalized).first() if normalized else None
    if eval_set is None:
        matches = list(query.filter(name__iexact=reference.strip()).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError("eval_set_not_found", "Multiple eval sets match; use the eval set id.")
        eval_set = matches[0] if matches else None
    if eval_set is None:
        raise MCPError("eval_set_not_found", "The eval set was not found for this capability.")
    return eval_set


def _resolve_experiment(context: MCPContext, reference: str) -> OptimizerExperiment:
    normalized = _uuid(reference)
    if normalized is None:
        raise MCPError("optimizer_not_found", "Use the optimizer experiment id.")
    candidates = OptimizerCandidate.objects.select_related("eval_run").order_by("candidate_index")
    iterations = OptimizerIteration.objects.order_by("order").prefetch_related(
        Prefetch("candidates", queryset=candidates)
    )
    experiment = (
        OptimizerExperiment.objects.filter(project=context.project, id=normalized)
        .select_related("capability", "dataset", "cell", "eval_set")
        .prefetch_related(Prefetch("iterations", queryset=iterations))
        .first()
    )
    if experiment is None:
        raise MCPError(
            "optimizer_not_found", "The optimizer experiment was not found in this project."
        )
    return experiment


def _validation_message(error: DRFValidationError) -> str:
    detail = error.detail
    if isinstance(detail, dict):
        values = [str(value) for value in detail.values()]
        if values:
            return values[0][:500]
    if isinstance(detail, list) and detail:
        return str(detail[0])[:500]
    return "The optimizer request failed validation."


def _executioner() -> OptimizerExecutionerState:
    return OptimizerExecutionerState(connected=False)


def _credits_available(user) -> bool:
    from overbae.api.credit_gate import require_credits

    try:
        require_credits(user)
    except PaymentRequired:
        return False
    return True


def _plan_available(user) -> bool:
    from overbae.services.plan_limits import require_plan_quota

    try:
        require_plan_quota(user, "optimize_runs")
    except PlanLimitExceeded:
        return False
    return True


def _model_readiness(
    mode: str, model_ids: list[str], capability: Capability
) -> OptimizerModelReadiness:
    try:
        validated = validate_optimizer_models(mode, model_ids, project=capability.project)
    except DRFValidationError as error:
        return OptimizerModelReadiness(
            mode=mode,
            requested=model_ids,
            valid=False,
            issue=_validation_message(error),
        )
    if mode in {
        OptimizerExperiment.Mode.MODEL_COMPARISON,
        OptimizerExperiment.Mode.HYBRID,
    } and not getattr(settings, "OPENROUTER_API_KEY", ""):
        return OptimizerModelReadiness(
            mode=mode,
            requested=model_ids,
            validated=validated,
            valid=False,
            issue="Overmind model access is not configured on this server.",
        )
    return OptimizerModelReadiness(
        mode=mode,
        requested=model_ids,
        validated=validated,
        valid=True,
    )


def _dataset_readiness(
    capability: Capability, dataset: Dataset | None, *, cell=None
) -> OptimizerDatasetReadiness | None:
    if dataset is None:
        return None
    issue = optimizer_dataset_error(capability, dataset, cell)
    chosen = cell or dataset.active_cell
    return OptimizerDatasetReadiness(
        id=str(dataset.id),
        name=dataset.name or str(dataset.id)[:8],
        intent=public_intent(dataset.intent),
        cell=mcp_cell_contract(dataset, chosen, "eval"),
        usable=issue is None,
        issue=issue,
    )


def _eval_set_readiness(
    capability: Capability, eval_set: EvalSet | None
) -> OptimizerEvalSetReadiness | None:
    if eval_set is None:
        return None
    return OptimizerEvalSetReadiness(
        id=str(eval_set.id),
        name=eval_set.name,
        capability=capability.slug,
        active=capability.active_eval_set_id == eval_set.id,
        member_count=eval_set.members.filter(enabled=True).count(),
    )


def _next_action(
    *,
    ready: bool,
    experiment_id: str | None = None,
) -> OptimizerNextAction:
    if not ready:
        return OptimizerNextAction(
            state="fix_prerequisites",
            message="Fix the listed optimizer prerequisites before starting a run.",
        )
    if experiment_id is None:
        return OptimizerNextAction(
            state="ready", message="The project is ready to start an optimizer run."
        )
    return OptimizerNextAction(
        state="run_executioner",
        message="The experiment is scheduled; drive it with the local CLI.",
        command=f"overmind optimise start -e {experiment_id} && overmind optimise next",
    )


def _readiness_sync(
    payload: CheckOptimizerReadinessInput, context: MCPContext
) -> CheckOptimizerReadinessOutput:
    capability = _resolve_capability(context, payload.capability)
    optimizer_prerequisite_report(context.project, capability)
    explicit_cell = None
    dataset = _resolve_dataset(context, payload.dataset) if payload.dataset else None
    if dataset is not None:
        explicit_cell = mcp_cell(dataset, _cell_ref(payload.cell, payload.version))
    if dataset is None:
        for candidate in Dataset.objects.filter(
            project=context.project, capability=capability, intent=Dataset.Intent.EVAL
        ).order_by("-created_at")[:10]:
            candidate_readiness = _dataset_readiness(capability, candidate)
            if candidate_readiness and candidate_readiness.usable:
                dataset = candidate
                break
    eval_set = (
        _resolve_eval_set(context, payload.eval_set, capability=capability)
        if payload.eval_set
        else capability.active_eval_set
    )
    dataset_data = _dataset_readiness(capability, dataset, cell=explicit_cell)
    eval_set_data = _eval_set_readiness(capability, eval_set)
    executioner = _executioner()
    models = _model_readiness(payload.mode, payload.model_ids, capability)
    credits = OptimizerCreditReadiness(available=_credits_available(context.user))
    plan = OptimizerPlanReadiness(available=_plan_available(context.user))

    missing: list[str] = []
    if dataset_data is None:
        missing.append("eval dataset")
    elif not dataset_data.usable:
        missing.append(f"eval dataset: {dataset_data.issue or 'not usable'}")
    if eval_set_data is None:
        missing.append("eval set")
    if not models.valid:
        missing.append(f"model selection: {models.issue or 'invalid'}")
    if not credits.available:
        missing.append("credits")
    if not plan.available:
        missing.append("plan quota")

    ready = not missing
    links = [resource_link("capabilities", str(capability.id), capability.name)]
    if dataset_data is not None:
        links.append(resource_link("datasets", dataset_data.id, dataset_data.name))
    return CheckOptimizerReadinessOutput(
        summary="Optimizer is ready." if ready else "Optimizer is not ready.",
        ready=ready,
        missing=missing,
        capability=links[0],
        dataset=dataset_data,
        eval_set=eval_set_data,
        executioner=executioner,
        models=models,
        credits=credits,
        plan=plan,
        next_action=_next_action(ready=ready),
        resource_links=links,
    )


def _start_sync(payload: StartOptimizerInput, context: MCPContext) -> StartOptimizerOutput:
    readiness = _readiness_sync(
        CheckOptimizerReadinessInput(
            capability=payload.capability,
            dataset=payload.dataset,
            eval_set=payload.eval_set,
            cell=payload.cell,
            version=payload.version,
            mode=payload.mode,
            model_ids=payload.model_ids,
        ),
        context,
    )
    if not readiness.ready:
        raise MCPError(
            "optimizer_not_ready",
            "The optimizer is not ready to start.",
            fields={"missing": ", ".join(readiness.missing)[:500]},
        )
    capability = _resolve_capability(context, payload.capability)
    dataset = _resolve_dataset(context, payload.dataset)
    cell = mcp_cell(dataset, _cell_ref(payload.cell, payload.version))
    eval_set = (
        _resolve_eval_set(context, payload.eval_set, capability=capability)
        if payload.eval_set
        else capability.active_eval_set
    )
    try:
        experiment = create_optimizer_experiment(
            user=context.user,
            capability=capability,
            dataset=dataset,
            cell=cell,
            eval_set=eval_set,
            entrypoint=payload.entrypoint,
            code_trigger=payload.code_trigger,
            num_iterations=payload.num_iterations,
            num_candidates_per_iteration=payload.num_candidates_per_iteration,
            max_iterations_without_improvement=payload.max_iterations_without_improvement,
            mode=payload.mode,
            model_ids=payload.model_ids,
            openrouter_key_source=OptimizerExperiment.OpenRouterKeySource.PLATFORM,
        )
    except PaymentRequired as error:
        raise MCPError(
            "insufficient_credits", "This operation requires available credits."
        ) from error
    except DRFValidationError as error:
        raise MCPError(
            "optimizer_invalid",
            "The optimizer request failed validation.",
            fields={"request": _validation_message(error)},
        ) from error
    except PlanLimitExceeded as error:
        raise MCPError(
            "plan_limit_exceeded", "The optimizer plan quota has been reached."
        ) from error

    executioner = _executioner()
    exp_link = resource_link("optimizer-runs", str(experiment.id), f"Optimizer {capability.name}")
    job_link = resource_link(
        "jobs", f"optimizer_experiment/{experiment.id}", f"Optimizer {capability.name}"
    )
    experiment_ref = OptimizerExperimentReference(
        id=str(experiment.id),
        status=experiment.status,
        mode=experiment.mode,
        capability=resource_link("capabilities", str(capability.id), capability.name),
        cell=mcp_cell_contract(
            experiment.dataset or dataset,
            experiment.cell or cell,
            "eval",
        ),
        resource=exp_link,
    )
    return StartOptimizerOutput(
        summary="Optimizer experiment scheduled.",
        experiment_id=str(experiment.id),
        experiment=experiment_ref,
        job=OptimizerJobReference(
            id=str(experiment.id), status=experiment.status, resource=job_link
        ),
        executioner=executioner,
        next_action=_next_action(
            ready=True,
            experiment_id=str(experiment.id),
        ),
        resource_links=[exp_link, job_link, experiment_ref.capability],
    )


def _candidate_coverage_fields(candidate: OptimizerCandidate) -> dict:
    scores = candidate.scores or {}
    coverage = scores.get("coverage") or {}
    measurement = scores.get("measurement") or {}
    uncovered = list(measurement.get("uncovered_card_claims") or [])
    return {
        "coverage_rate": scores.get("coverage_rate", coverage.get("coverage_rate")),
        "graded_rows": scores.get("graded_rows", coverage.get("graded_rows")),
        "total_rows": scores.get("total_rows", coverage.get("total_rows")),
        "suite_incomplete": bool(uncovered),
        "uncovered_card_claims": uncovered,
    }


def _candidate_result(candidate: OptimizerCandidate) -> OptimizerCandidateResult:
    patch = (candidate.code_path or "")[:_PATCH_CAP]
    eval_link = (
        resource_link("eval-runs", str(candidate.eval_run_id), candidate.eval_run.name)
        if candidate.eval_run_id and candidate.eval_run is not None
        else None
    )
    return OptimizerCandidateResult(
        id=str(candidate.id),
        index=candidate.candidate_index,
        status=candidate.status,
        is_baseline=candidate.is_baseline,
        target_model=candidate.target_model or None,
        score=candidate.score,
        **_candidate_coverage_fields(candidate),
        scores=safe_json(candidate.scores or {}),
        patch=patch or None,
        patch_truncated=len(candidate.code_path or "") > _PATCH_CAP,
        eval_run=eval_link,
    )


def _winner(
    experiment: OptimizerExperiment, candidates: list[OptimizerCandidate]
) -> OptimizerWinner | None:
    state = experiment.state if isinstance(experiment.state, dict) else {}
    winner_id = str(state.get("winner_candidate_id") or "")
    selected = next((candidate for candidate in candidates if str(candidate.id) == winner_id), None)
    if selected is None and winner_id:
        selected = (
            OptimizerCandidate.objects.filter(experiment=experiment, id=winner_id)
            .select_related("eval_run")
            .first()
        )
    comparison = state.get("model_comparison")
    if selected is None and experiment.mode == OptimizerExperiment.Mode.MODEL_COMPARISON:
        if isinstance(comparison, dict) and comparison.get("overall_winner") == "incumbent":
            return OptimizerWinner(
                target_model=None,
                score=float(comparison.get("incumbent_score") or 0.0),
                kind="incumbent",
            )
        selected_model = comparison.get("selected_winner") if isinstance(comparison, dict) else None
        if selected_model:
            selected = (
                OptimizerCandidate.objects.filter(
                    experiment=experiment, target_model=selected_model
                )
                .select_related("eval_run")
                .order_by("-score", "-iteration__order", "candidate_index")
                .first()
            )
    if selected is None:
        eligible = [candidate for candidate in candidates if not candidate.is_baseline]
        if experiment.mode == OptimizerExperiment.Mode.OPTIMIZE:
            baseline = float((experiment.scores or {}).get("baseline") or 0.0)
            eligible = [
                candidate
                for candidate in eligible
                if candidate.code_path and candidate.score > baseline
            ]
        selected = max(eligible, key=candidate_comparison_rank, default=None)
    if selected is not None:
        return OptimizerWinner(
            candidate_id=str(selected.id),
            target_model=selected.target_model or None,
            score=selected.score,
            kind="candidate",
        )
    comparison = state.get("model_comparison") if isinstance(state, dict) else None
    if isinstance(comparison, dict) and comparison.get("overall_winner") == "incumbent":
        return OptimizerWinner(
            target_model=None,
            score=float(comparison.get("incumbent_score") or 0.0),
            kind="incumbent",
        )
    return None


def _inspect_sync(
    payload: InspectOptimizerResultInput, context: MCPContext
) -> InspectOptimizerResultOutput:
    experiment = _resolve_experiment(context, payload.experiment)
    all_iterations = list(experiment.iterations.order_by("order"))
    iterations = all_iterations[: payload.max_iterations]
    result_iterations: list[OptimizerIterationResult] = []
    all_candidates: list[OptimizerCandidate] = []
    for iteration in iterations:
        all_candidates.extend(list(iteration.candidates.order_by("candidate_index")))
        candidates = list(iteration.candidates.order_by("candidate_index"))
        bounded = candidates[: payload.max_candidates_per_iteration]
        result_iterations.append(
            OptimizerIterationResult(
                id=str(iteration.id),
                order=iteration.order,
                name=iteration.name
                or ("Baseline" if iteration.order == 0 else f"Iteration {iteration.order}"),
                status=iteration.status,
                scores=safe_json(iteration.scores or {}),
                candidates=[_candidate_result(candidate) for candidate in bounded],
                candidates_truncated=len(candidates) > len(bounded),
            )
        )

    executioner = _executioner()
    exp_link = resource_link(
        "optimizer-runs", str(experiment.id), f"Optimizer {experiment.capability.name}"
    )
    job_link = resource_link(
        "jobs", f"optimizer_experiment/{experiment.id}", f"Optimizer {experiment.capability.name}"
    )
    summary = OptimizerResultSummary(
        id=str(experiment.id),
        status=experiment.status,
        mode=experiment.mode,
        capability=resource_link(
            "capabilities", str(experiment.capability.id), experiment.capability.name
        ),
        cell=mcp_cell_contract(experiment.dataset, experiment.cell, "eval")
        if experiment.dataset_id
        else None,
        current_iteration=experiment.current_iteration,
        num_iterations=experiment.num_iterations,
        scores=safe_json(experiment.scores or {}),
        failure_reason=(experiment.failure_reason or "")[:1_000] or None,
        resource=exp_link,
    )
    terminal = experiment.status in {
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
    }
    next_action = OptimizerNextAction(
        state="inspect_result"
        if terminal
        else _next_action(ready=True, experiment_id=str(experiment.id)).state,
        message="The optimizer experiment is terminal."
        if terminal
        else "Drive the scheduled experiment with the local CLI.",
        command=None
        if terminal
        else f"overmind optimise start -e {experiment.id} && overmind optimise next",
    )
    return InspectOptimizerResultOutput(
        summary=f"Optimizer experiment {experiment.status}.",
        experiment=summary,
        iterations=result_iterations,
        iterations_truncated=len(all_iterations) > len(iterations),
        winner=_winner(experiment, all_candidates),
        executioner=executioner,
        next_action=next_action,
        resource_links=[exp_link, job_link, summary.capability],
    )


def _async_handler(function: Callable):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_optimizer_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "check_optimizer_readiness",
            "Check optimizer readiness",
            "Inspect project-scoped optimizer dataset, eval set, model, credit, and plan readiness.",
            CheckOptimizerReadinessInput,
            CheckOptimizerReadinessOutput,
            _readiness_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "start_optimizer",
            "Start optimizer",
            "Validate and schedule an optimizer, model comparison, or hybrid experiment; drive it with `overmind optimise`.",
            StartOptimizerInput,
            StartOptimizerOutput,
            _start_sync,
            False,
            True,
            "llm",
            "job",
            {"overmind:optimize"},
        ),
        (
            "inspect_optimizer_result",
            "Inspect optimizer result",
            "Read a bounded project-scoped optimizer experiment with iteration scores, candidate patches, winner state, and next action.",
            InspectOptimizerResultInput,
            InspectOptimizerResultOutput,
            _inspect_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
    ]
    for (
        name,
        title,
        description,
        input_model,
        output_model,
        function,
        read_only,
        idempotent,
        cost_class,
        async_mode,
        scopes,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=idempotent if name != "start_optimizer" else False,
                open_world=name == "start_optimizer",
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
