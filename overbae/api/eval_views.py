from __future__ import annotations

import logging

from django.db.models import (
    Case,
    CharField,
    Exists,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
    When,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import EvalPagination
from overbae.api.eval_serializers import (
    AuthorJudgeEvaluatorRequestSerializer,
    EvalRunListSerializer,
    EvalRunSerializer,
    EvalSampleListSerializer,
    EvalSampleSerializer,
    EvalSetAddMembersRequestSerializer,
    EvalSetMemberUpdateSerializer,
    EvalSetSerializer,
    EvaluatorCatalogSerializer,
    EvaluatorListSerializer,
    EvaluatorScoreHistorySerializer,
    EvaluatorSerializer,
    GenerateEvaluatorPromptRequestSerializer,
    GenerateEvaluatorPromptResponseSerializer,
    ScoreSerializer,
    VerdictSerializer,
)
from overbae.api.filters import (
    EvalRunFilter,
    EvalSampleFilter,
    EvaluatorFilter,
    ScoreFilter,
    VerdictFilter,
)
from overbae.api.serializers import ModelCatalogResponseSerializer
from overbae.api.views import _user_project_ids
from overbae.core.model_registry import model_defaults
from overbae.models import (
    Capability,
    EvalRun,
    EvalSample,
    EvalSet,
    EvalSetMember,
    Evaluator,
    Score,
    Verdict,
)
from overbae.services.model_catalog import fetch_model_catalog

logger = logging.getLogger(__name__)


class ModelCatalogView(APIView):
    @extend_schema(
        summary="Browse the OpenRouter model catalog",
        description=(
            "Trimmed OpenRouter model list for the eval wizard's model picker. "
            "Each `id` is the exact `model_name` an eval variant should use. "
            "On upstream failure `upstream_available` is false and `models` is empty."
        ),
        responses=ModelCatalogResponseSerializer,
    )
    def get(self, request):
        models, upstream_available = fetch_model_catalog()
        serializer = ModelCatalogResponseSerializer(
            {
                "models": models,
                "upstream_available": upstream_available,
                "defaults": model_defaults(),
            }
        )
        return Response(serializer.data)


def _reject_authored_name_collision(project, capability, name) -> None:
    """A hand-authored evaluator sharing a name with a managed/machine-authored
    one would land in the same (project, capability, name) version lineage and
    silently become its "latest" — shadowing it in the catalog and in
    ``library_for_project``. Reject rather than let that happen quietly."""
    from overbae.services.eval.specs import AUTHORED_GENERATORS

    clash = (
        Evaluator.objects.filter(
            Q(project=project, capability=capability)
            | Q(is_managed=True, project__isnull=True, capability__isnull=True),
            name=name,
        )
        .order_by("-version")
        .first()
    )
    if clash is None:
        return
    generator = ((clash.config or {}).get("provenance") or {}).get("generator")
    if clash.is_managed or generator in AUTHORED_GENERATORS:
        raise ValidationError(
            {
                "name": (
                    f'"{name}" is already used by a managed or auto-authored evaluator. '
                    "Choose a different name."
                )
            }
        )


def _attach_to_eval_set_if_requested(evaluator, eval_set, role) -> None:
    """Attach-on-save: skips the second .../members/ round-trip the create
    dialog would otherwise need. Same applicability guard as the ``members``
    action — rejected, never silently skipped."""
    if eval_set is None:
        return
    from overbae.services.eval.eval_set import add_evaluator_as_member
    from overbae.services.eval.roles import roles_for_evaluator

    applicable = roles_for_evaluator(evaluator)
    if role not in applicable:
        raise ValidationError(
            {
                "eval_set_role": (
                    f"'{evaluator.name}' ({evaluator.scope}) is not applicable to the "
                    f"'{role}' role; applicable role(s): {', '.join(applicable)}."
                )
            }
        )
    add_evaluator_as_member(eval_set, evaluator, role)


@extend_schema_view(
    list=extend_schema(summary="List evaluators (managed + project)"),
    retrieve=extend_schema(summary="Get evaluator"),
    create=extend_schema(summary="Create evaluator (new immutable version)"),
)
class EvaluatorViewSet(viewsets.ModelViewSet):
    filterset_class = EvaluatorFilter
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "name", "version"]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.action == "list":
            return EvaluatorListSerializer
        return EvaluatorSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Evaluator.objects.none()
        project_ids = _user_project_ids(self.request.user)
        qs = Evaluator.objects.filter(project_id__in=project_ids)
        # Managed templates are global (project IS NULL) and visible to everyone.
        if str(self.request.query_params.get("include_managed", "true")).lower() != "false":
            from django.db.models import Q  # noqa: PLC0415

            qs = Evaluator.objects.filter(
                Q(project_id__in=project_ids) | Q(is_managed=True, project__isnull=True)
            )
        return qs

    def perform_create(self, serializer):
        # A save against an existing (project, name) bumps version — rows are
        # immutable, never updated in place.
        project = serializer.validated_data.get("project")
        name = serializer.validated_data.get("name")
        latest = (
            Evaluator.objects.filter(project=project, name=name)
            .aggregate(v=Max("version"))
            .get("v")
        )
        serializer.save(created_by=self.request.user, version=(latest or 0) + 1)

    @extend_schema(
        summary="Catalog every evaluator (generic + bespoke) for the evals page",
        description=(
            "Lists every evaluator visible to the caller — project-library "
            "(`capability=null`, tagged generic) AND capability-scoped (bespoke) — with the "
            "internal `__agent_spec__` / `__structure__` / `__tool_usage__` "
            "sentinels excluded and the version history collapsed to the latest "
            "live row. Optional `?capability=<id>` narrows to one capability's bespoke evals "
            "(the evals-page deep link); `?search=` filters by name/description."
        ),
        parameters=[
            OpenApiParameter(
                name="project",
                description="Scope to one project (global managed templates stay visible).",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="capability",
                description="Restrict to a single capability's bespoke evaluators.",
                required=False,
                type=str,
            ),
            OpenApiParameter(
                name="search",
                description="Case-insensitive filter over name + description.",
                required=False,
                type=str,
            ),
        ],
        responses={200: EvaluatorCatalogSerializer(many=True)},
    )
    # No pagination: the evals-page grid filters and searches client-side, so it
    # needs the whole catalog in one response. It is one row per live evaluator,
    # not the score fan-out EvalPagination exists for.
    @action(detail=False, methods=["get"], url_path="catalog", pagination_class=None)
    def catalog(self, request):
        from django.db.models import Q  # noqa: PLC0415

        project_ids = _user_project_ids(request.user)
        qs = Evaluator.objects.filter(
            Q(project_id__in=project_ids) | Q(is_managed=True, project__isnull=True)
        )
        project_id = request.query_params.get("project")
        if project_id:
            # Global managed templates stay visible inside a project scope.
            qs = qs.filter(Q(project_id=project_id) | Q(is_managed=True, project__isnull=True))
        qs = qs.visible_catalog()

        capability_id = request.query_params.get("capability")
        if capability_id:
            # The OR is required: a bare ``capability_id=`` match hides the whole
            # reusable library (managed templates + project-level graders) and
            # leaves the "Add from library" picker nearly empty.
            qs = qs.filter(Q(capability_id=capability_id) | Q(capability__isnull=True))
        search = (request.query_params.get("search") or "").strip()
        if search:
            qs = qs.filter(Q(name__icontains=search) | Q(description__icontains=search))

        # One extra query for every evaluator's latest + previous run score,
        # instead of an N+1 fan-out across the catalog.
        from overbae.api.eval_serializers import resolve_evaluator_run_scores  # noqa: PLC0415

        evaluators = list(qs)
        scoped_capability_ids = {str(ev.capability_id) for ev in evaluators if ev.capability_id}
        # That capability's runs also score the shared evaluators the picker offers,
        # so capability-less rows resolve against this capability's history.
        if capability_id:
            scoped_capability_ids.add(str(capability_id))
        scores = resolve_evaluator_run_scores(scoped_capability_ids)
        serializer = EvaluatorCatalogSerializer(
            evaluators,
            many=True,
            context={"evaluator_scores": scores, "score_capability_id": capability_id},
        )
        return Response(serializer.data)

    @extend_schema(
        summary="Per-evaluator score history for a capability (time series)",
        description=(
            "Returns one score time series per evaluator (keyed by name) for the "
            "given capability, built from every COMPLETED eval run's stored summary "
            "rollup (a run belongs to a capability via `dataset.capability`). Each series "
            "carries `points` of `{run_id, run_at, score}` ordered oldest→newest "
            "with `score` normalized to 0–100, plus the capability's current live "
            "`evaluator_id` for that name when one exists. Powers the capability "
            "eval-metrics score-history line chart; the time-window filter is "
            "applied client-side."
        ),
        parameters=[
            OpenApiParameter(
                name="capability",
                description="Capability whose evaluator score history to return (required).",
                required=True,
                type=str,
            ),
        ],
        responses={200: EvaluatorScoreHistorySerializer(many=True)},
    )
    @action(detail=False, methods=["get"], url_path="score-history", pagination_class=None)
    def score_history(self, request):
        capability_id = request.query_params.get("capability")
        if not capability_id:
            raise ValidationError({"capability": "This query parameter is required."})
        project_ids = _user_project_ids(request.user)
        if not Capability.objects.filter(id=capability_id, project_id__in=project_ids).exists():
            raise NotFound("Capability not found.")

        from overbae.api.eval_serializers import resolve_evaluator_score_history  # noqa: PLC0415

        history = resolve_evaluator_score_history(capability_id)
        serializer = EvaluatorScoreHistorySerializer(history, many=True)
        return Response(serializer.data)

    @extend_schema(
        summary="Author a runnable LLM-judge evaluator from the create dialog",
        description=(
            "Persists a new ``llm_judge`` Evaluator from the structured authoring "
            "payload (name, optional judge model, evaluation prompt, score type + "
            "type-specific config). Maps NUMERIC/BOOLEAN/CATEGORICAL onto the model "
            "so it scores via the existing judge path, and appears in the catalog."
        ),
        request=AuthorJudgeEvaluatorRequestSerializer,
        responses={201: EvaluatorSerializer},
    )
    @action(detail=False, methods=["post"], url_path="author")
    def author(self, request):
        from django.db import transaction

        from overbae.services.eval.authored import author_judge_evaluator

        req = AuthorJudgeEvaluatorRequestSerializer(data=request.data, context={"request": request})
        req.is_valid(raise_exception=True)
        data = req.validated_data
        _reject_authored_name_collision(data["project"], data.get("capability"), data["name"])
        with transaction.atomic():
            evaluator = author_judge_evaluator(data, request.user)
            _attach_to_eval_set_if_requested(
                evaluator, data.get("eval_set"), data.get("eval_set_role")
            )
        return Response(
            EvaluatorSerializer(evaluator, context={"request": request}).data, status=201
        )

    @extend_schema(
        summary="Edit an existing LLM-judge evaluator (re-compose from authoring inputs)",
        description=(
            "Updates an ``llm_judge`` evaluator in place from the same authoring "
            "payload the create dialog uses — re-composing rubric_md/config/choices/"
            "score_type/judge_model/name/capability. Edits in place (keeps the same id "
            "and version) so catalog rows, score history and eval-set members keep "
            "referencing this evaluator; versioning is reserved for the generative "
            "regeneration pipeline. Only LLM-judge evaluators are editable here."
        ),
        request=AuthorJudgeEvaluatorRequestSerializer,
        responses={200: EvaluatorSerializer},
    )
    @action(detail=True, methods=["put"], url_path="author")
    def author_update(self, request, id=None):
        from django.db import transaction

        from overbae.services.eval.rubric_compiler import (
            attach_compiled_checklist,
            compose_judge_evaluator_kwargs,
        )

        evaluator = self.get_object()
        if evaluator.kind != "llm_judge":
            raise ValidationError("Only LLM-judge evaluators can be edited here.")
        # Managed templates are shared platform-wide; one project must not mutate
        # them through the authoring dialog.
        if evaluator.is_managed and evaluator.project_id is None:
            raise PermissionDenied("Managed templates cannot be edited.")

        req = AuthorJudgeEvaluatorRequestSerializer(data=request.data, context={"request": request})
        req.is_valid(raise_exception=True)
        data = req.validated_data
        kwargs = attach_compiled_checklist(compose_judge_evaluator_kwargs(data))
        # In place, so catalog rows, score history and eval-set members keep
        # referencing this id and version.
        for field in (
            "capability",
            "name",
            "description",
            "scope",
            "rubric_md",
            "checklist",
            "variable_mapping",
            "judge_model",
            "score_type",
            "score_min",
            "score_max",
            "choices",
            "config",
            "applicable_roles",
            "evidence_requirement",
        ):
            setattr(evaluator, field, kwargs[field])
        with transaction.atomic():
            evaluator.save()
            _attach_to_eval_set_if_requested(
                evaluator, data.get("eval_set"), data.get("eval_set_role")
            )
        return Response(EvaluatorSerializer(evaluator, context={"request": request}).data)

    @extend_schema(
        summary="Generate an evaluation prompt from a description (optionally capability-grounded)",
        description=(
            "Runs the existing grounding pipeline (resolve_grounding_for_capability + "
            "render_grounding_pack) for the optional capability and asks the criteria "
            "model to author one evaluation prompt for the description. Returns the "
            "prompt for the user to review/edit before saving. Generic when no capability."
        ),
        request=GenerateEvaluatorPromptRequestSerializer,
        responses={200: GenerateEvaluatorPromptResponseSerializer},
    )
    @action(detail=False, methods=["post"], url_path="generate-prompt")
    def generate_prompt(self, request):
        from overbae.api.credit_gate import require_credits
        from overbae.services.eval.rubric_compiler import generate_evaluation_prompt

        req = GenerateEvaluatorPromptRequestSerializer(
            data=request.data, context={"request": request}
        )
        req.is_valid(raise_exception=True)
        capability = req.validated_data.get("capability")
        if capability is not None and capability.project_id not in _user_project_ids(request.user):
            raise NotFound("Capability not found.")
        require_credits(request.user)
        result = generate_evaluation_prompt(
            req.validated_data["description"],
            capability=capability,
            applicable_role=req.validated_data.get("applicable_role") or "generative",
        )
        return Response(result)


@extend_schema_view(
    list=extend_schema(summary="List eval sets (capability-grouped rubric sets)"),
    retrieve=extend_schema(summary="Get an eval set with its members"),
    create=extend_schema(summary="Create an eval set for a capability"),
    update=extend_schema(summary="Update an eval set"),
    partial_update=extend_schema(summary="Partially update an eval set"),
    destroy=extend_schema(summary="Delete an eval set"),
)
class EvalSetViewSet(viewsets.ModelViewSet):
    """A set groups a capability's runnable evaluators by role. The *active* set
    drives the optimizer, the backtest, and the run wizard's default selection —
    see ``runnable_capability_evaluators``.
    """

    serializer_class = EvalSetSerializer
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "name"]
    lookup_field = "id"

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalSet.objects.none()
        # Soft-deleted capabilities are hidden from the capability API, so surfacing their
        # sets strands the "Manage sets" link on a 404.
        qs = (
            EvalSet.objects.filter(
                Q(capability__status="current") | Q(capability__isnull=True),
                project_id__in=_user_project_ids(self.request.user),
            )
            .select_related("capability")
            .prefetch_related(
                # ``eval_set`` is joined here because the member serializer reads
                # the owning capability — otherwise one back-reference query per member.
                Prefetch(
                    "members",
                    queryset=EvalSetMember.objects.select_related(
                        "evaluator__capability", "eval_set"
                    ),
                )
            )
        )
        capability_id = self.request.query_params.get("capability")
        if capability_id:
            qs = qs.filter(Q(capability_id=capability_id) | Q(capability__isnull=True))
        return qs

    def get_serializer_context(self):
        # One extra query over the relevant capabilities' completed runs, whose stored
        # summaries already carry the aggregates — no recompute, no N+1.
        ctx = super().get_serializer_context()
        if getattr(self, "swagger_fake_view", False):
            return ctx
        from overbae.api.eval_serializers import resolve_evaluator_run_scores

        ctx["evaluator_scores"] = resolve_evaluator_run_scores(self._scored_capability_ids())
        return ctx

    def _scored_capability_ids(self) -> list[str]:
        """Capability ids whose completed-run scores feed the rendered member rows.

        Narrowed to the request so the resolver query stays bounded.
        """
        lookup = self.kwargs.get(self.lookup_field)
        if lookup:
            return list(
                self.get_queryset()
                .filter(**{self.lookup_field: lookup})
                .values_list("capability_id", flat=True)
            )
        capability_id = self.request.query_params.get("capability")
        if capability_id:
            return [capability_id]
        return list(self.get_queryset().values_list("capability_id", flat=True).distinct())

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    def perform_destroy(self, instance):
        # The FK is SET_NULL, so this only exists to hand the active pointer to
        # another set rather than leave the capability with none.
        capability = instance.capability
        if capability is not None and capability.active_eval_set_id == instance.id:
            replacement = (
                EvalSet.objects.filter(capability=capability)
                .exclude(id=instance.id)
                .order_by("-created_at")
                .first()
            )
            capability.active_eval_set = replacement
            capability.save(update_fields=["active_eval_set"])
        instance.delete()

    @staticmethod
    def _next_order(eval_set) -> int:
        current = eval_set.members.aggregate(m=Max("order")).get("m")
        return (current + 1) if current is not None else 0

    @extend_schema(
        summary="Add members to the set (authored specs and/or existing evaluators)",
        request=EvalSetAddMembersRequestSerializer,
        responses={201: EvalSetSerializer},
    )
    @action(detail=True, methods=["post"], url_path="members")
    def add_members(self, request, id=None):
        from overbae.services.eval.authored import persist_specs
        from overbae.services.eval.roles import roles_for_evaluator, roles_for_spec
        from overbae.services.eval.specs import validate_spec_payloads

        eval_set = self.get_object()
        req = EvalSetAddMembersRequestSerializer(data=request.data)
        req.is_valid(raise_exception=True)
        role = req.validated_data["role"]

        evaluators: list[Evaluator] = list(req.validated_data.get("evaluator_ids", []))
        # Rejected, never silently skipped: a generative grader scores produced
        # output, a trace_scoring grader scores live-trace structure.
        inapplicable = [ev for ev in evaluators if role not in roles_for_evaluator(ev)]
        if inapplicable:
            raise ValidationError(
                {
                    "evaluator_ids": [
                        f"'{ev.name}' ({ev.scope}) is not applicable to the "
                        f"'{role}' role; applicable role(s): "
                        f"{', '.join(roles_for_evaluator(ev))}."
                        for ev in inapplicable
                    ]
                }
            )

        specs, errors = validate_spec_payloads(req.validated_data.get("specs", []))
        # The generate flow already splits by applicability; this guards a
        # hand-crafted payload.
        bad_specs = [s for s in specs if role not in roles_for_spec(s)]
        if bad_specs:
            raise ValidationError(
                {
                    "specs": [
                        f"'{s.name}' ({s.scope}) is not applicable to the '{role}' role."
                        for s in bad_specs
                    ]
                }
            )
        if specs:
            evaluators += persist_specs(
                specs,
                project=eval_set.project,
                capability=eval_set.capability,
                created_by=request.user,
            )

        # A second row with the same NAME is the duplication the (set, evaluator,
        # role) constraint cannot see, and run aggregates are keyed by name — two
        # rows silently double-weight that metric in every average.
        taken = {
            m.evaluator.name: m.evaluator_id
            for m in EvalSetMember.objects.filter(eval_set=eval_set, role=role).select_related(
                "evaluator"
            )
        }
        clashing = [ev for ev in evaluators if taken.get(ev.name, ev.id) != ev.id]
        if clashing:
            raise ValidationError(
                {
                    "evaluator_ids": [
                        f"'{ev.name}' is already a '{role}' member of this set as a "
                        "different evaluator row. Remove that member first."
                        for ev in clashing
                    ]
                }
            )

        # Idempotent: an existing (set, evaluator, role) is skipped rather than
        # 400-ing the batch, so re-adding is a no-op.
        order = self._next_order(eval_set)
        for evaluator in evaluators:
            _, created = EvalSetMember.objects.get_or_create(
                eval_set=eval_set,
                evaluator=evaluator,
                role=role,
                defaults={"prompt": None, "order": order},
            )
            if created:
                order += 1

        eval_set.refresh_from_db()
        return Response(
            EvalSetSerializer(eval_set, context=self.get_serializer_context()).data,
            status=201,
        )

    @extend_schema(
        summary="Update or remove one set member",
        request=EvalSetMemberUpdateSerializer,
        responses={200: EvalSetSerializer},
    )
    @action(
        detail=True,
        methods=["patch", "delete"],
        url_path=r"members/(?P<member_id>[0-9a-f-]{36})",
    )
    def modify_member(self, request, id=None, member_id=None):
        eval_set = self.get_object()
        member = get_object_or_404(EvalSetMember, id=member_id, eval_set=eval_set)

        if request.method == "DELETE":
            member.delete()
        else:
            req = EvalSetMemberUpdateSerializer(data=request.data)
            req.is_valid(raise_exception=True)
            for field, value in req.validated_data.items():
                setattr(member, field, value)
            member.save(update_fields=list(req.validated_data.keys()) or None)

        eval_set.refresh_from_db()
        return Response(EvalSetSerializer(eval_set, context=self.get_serializer_context()).data)

    @extend_schema(
        summary="Activate this set on its capability",
        description="Point the owning capability's ``active_eval_set`` at this set.",
        request=None,
        responses={200: EvalSetSerializer},
    )
    @action(detail=True, methods=["post"], url_path="activate")
    def activate(self, request, id=None):
        from overbae.services.eval.eval_set import activate as activate_set

        eval_set = self.get_object()
        if eval_set.capability_id is None:
            raise ValidationError({"capability": "Assign a capability before activating this set."})
        activate_set(eval_set.capability, eval_set)
        eval_set.refresh_from_db()
        return Response(EvalSetSerializer(eval_set, context=self.get_serializer_context()).data)


@extend_schema_view(
    list=extend_schema(summary="List evaluation runs"),
    retrieve=extend_schema(summary="Get evaluation run (with variants + summary)"),
    create=extend_schema(summary="Create + launch an evaluation run"),
)
class EvalRunViewSet(viewsets.ModelViewSet):
    filterset_class = EvalRunFilter
    search_fields = ["name", "description"]
    ordering_fields = ["created_at", "completed_at", "name", "status"]
    lookup_field = "id"

    def get_serializer_class(self):
        if self.action == "list":
            return EvalRunListSerializer
        return EvalRunSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalRun.objects.none()
        from overbae.models.finetuning import FinetuningJobEval
        from overbae.models.optimizer import OptimizerCandidate, OptimizerIteration

        # Read by EvalRunListSerializer to split optimizer runs from plain and
        # fine-tuning ones.
        origin = Case(
            When(
                Exists(OptimizerCandidate.objects.filter(eval_run=OuterRef("pk")))
                | Exists(OptimizerIteration.objects.filter(eval_run=OuterRef("pk"))),
                then=Value("optimizer"),
            ),
            When(
                Exists(FinetuningJobEval.objects.filter(eval_run=OuterRef("pk"))),
                then=Value("finetuning"),
            ),
            default=Value("manual"),
            output_field=CharField(),
        )
        # Candidate link wins; the iteration link is the legacy shape.
        optimizer_experiment = Coalesce(
            Subquery(
                OptimizerCandidate.objects.filter(eval_run=OuterRef("pk")).values("experiment_id")[
                    :1
                ]
            ),
            Subquery(
                OptimizerIteration.objects.filter(eval_run=OuterRef("pk")).values("experiment_id")[
                    :1
                ]
            ),
        )
        return (
            EvalRun.objects.filter(project_id__in=_user_project_ids(self.request.user))
            .select_related("dataset")
            .prefetch_related("variants", "evaluators")
            .annotate(origin=origin, optimizer_experiment=optimizer_experiment)
        )

    def perform_create(self, serializer):
        from overbae.api.credit_gate import require_credits
        from overbae.tasks.eval import run_eval_run

        require_credits(self.request.user)

        run = serializer.save(triggered_by=self.request.user)
        # A broker hiccup must not 500 after the row exists; persist the failure
        # on the run so the UI shows why it never started.
        try:
            result = run_eval_run.apply_async(kwargs={"eval_run_id": str(run.id)})
        except Exception:  # noqa: BLE001
            logger.exception("eval run %s dispatch failed", run.id)
            EvalRun.objects.filter(pk=run.pk).update(
                status=EvalRun.Status.FAILED,
                error="Could not queue the run. Try re-launching it.",
            )
            return
        EvalRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)

    @extend_schema(
        summary="List distinct datasets used by evaluation runs visible to the caller",
        description=(
            "Option list for the runs table's dataset filter: one entry per dataset "
            "that has at least one eval run. Not the project's dataset list — a "
            "dataset with no runs is not an offerable filter value."
        ),
        parameters=[
            OpenApiParameter(
                name="project",
                description="Scope to one project.",
                required=False,
                type=str,
            )
        ],
        # Raw schema, not a serializer with many=True — spectacular would wrap
        # the latter in the viewset's pagination envelope, which this omits.
        responses={
            200: {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "format": "uuid"},
                        "name": {"type": "string"},
                    },
                    "required": ["id", "name"],
                },
            }
        },
    )
    @action(detail=False, methods=["get"], url_path="datasets")
    def datasets(self, request):
        # Read off the runs, not the dataset table, so the option list and the
        # ``?dataset=`` filter can never disagree about which values yield rows.
        # Bounded without pagination: DISTINCT over datasets, not runs.
        pairs = (
            self.filter_queryset(
                EvalRun.objects.filter(project_id__in=_user_project_ids(request.user))
            )
            .exclude(dataset=None)
            .values_list("dataset_id", "dataset__name")
            .distinct()
            .order_by("dataset__name")
        )
        return Response([{"id": str(dataset_id), "name": name} for dataset_id, name in pairs])

    @extend_schema(summary="Re-launch a run", request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def run(self, request, id=None):
        from overbae.api.credit_gate import require_credits
        from overbae.tasks.eval import run_eval_run

        run = self.get_object()
        require_credits(request.user)
        run.samples.all().delete()
        run.scores.all().delete()
        EvalRun.objects.filter(pk=run.pk).update(
            status=EvalRun.Status.PENDING, summary={}, error="", completed_at=None
        )
        try:
            result = run_eval_run.apply_async(kwargs={"eval_run_id": str(run.id)})
        except Exception:  # noqa: BLE001 — broker hiccup shouldn't surface as a bare 500
            logger.exception("eval run %s relaunch dispatch failed", run.id)
            EvalRun.objects.filter(pk=run.pk).update(
                status=EvalRun.Status.FAILED,
                error="Could not queue the run. Try re-launching it.",
            )
            return Response(
                {"detail": "Could not queue the run. Try again.", "code": "dispatch_failed"},
                status=503,
            )
        EvalRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)
        return Response({"status": "launched", "task_id": result.id})

    @extend_schema(summary="Cancel a run", request=None, responses={200: None})
    @action(detail=True, methods=["post"])
    def cancel(self, request, id=None):
        from overbae.tasks.eval import cancel_run

        run = self.get_object()
        # Revokes in-flight generation/orchestration tasks, so a hung run stops
        # occupying worker threads instead of lingering until it self-completes.
        revoked = cancel_run(run)
        return Response({"status": "cancelled", "revoked_tasks": revoked})

    @extend_schema(summary="Get the comparison summary (runs-as-columns)")
    @action(detail=True, methods=["get"])
    def comparison(self, request, id=None):
        run = self.get_object()
        return Response(_build_run_report(run, context=self.get_serializer_context()))


def _build_run_report(run, *, context) -> dict:
    """The full run-detail report payload (run-as-columns comparison grid).

    The whole :class:`EvalRunSerializer` payload is spread in because the
    generated client's ``EvalRunFromJSON`` requires every field — a missing
    ``run_evaluators`` makes its ``.map()`` throw and the query returns undefined.
    """
    if run.status in (EvalRun.Status.RUNNING, EvalRun.Status.PENDING):
        summary = _live_summary(run) or (run.summary or {})
    else:
        summary = run.summary or {}
    run_data = EvalRunSerializer(run, context=context).data
    # The serializer only computes progress for live runs; terminal runs are
    # backfilled here so the payload always carries the counts.
    progress = run_data.get("progress") or _run_progress(run)
    return {
        **run_data,
        # The frontend's ComparisonPayload still reads this key.
        "run_id": str(run.id),
        "summary": summary,
        "progress": progress,
    }


def _live_summary(run) -> dict:
    """Compute an interim comparison grid from scores that exist so far."""

    from overbae.models import EvalSample, Score  # noqa: PLC0415
    from overbae.services.eval import ranking  # noqa: PLC0415

    rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(name__endswith="__prediction")
        .select_related("variant")
    )
    score_dicts = [
        {
            "variant_id": str(s.variant_id),
            "variant_label": s.variant.label if s.variant_id else "",
            "name": s.name,
            "value": s.value,
            "passed": s.passed,
            "data_type": s.data_type,
            "scope": s.scope,
            "outcome": s.outcome,
        }
        for s in rows
    ]

    variant_sample_counts: dict[str, int] = {}
    for v in run.variants.all():
        variant_sample_counts[str(v.id)] = EvalSample.objects.filter(run=run, variant=v).count()

    for s in Score.objects.filter(run=run, scope="dataset").select_related("variant"):
        vid = str(s.variant_id) if s.variant_id else "__none__"
        sub = s.sub_scores or []
        n_from_sub = sub[0].get("n") if sub else None
        n_override = n_from_sub or variant_sample_counts.get(vid, 1)
        score_dicts.append(
            {
                "variant_id": vid,
                "variant_label": s.variant.label if s.variant_id else "",
                "name": s.name,
                "value": s.value,
                "passed": s.passed,
                "data_type": s.data_type,
                "scope": "dataset",
                "outcome": s.outcome,
                "n_override": n_override,
            }
        )

    if not score_dicts:
        return {}
    baseline = (
        run.variants.filter(is_baseline=True).first() or run.variants.order_by("order").first()
    )
    return ranking.rollup(score_dicts, baseline_variant_id=str(baseline.id) if baseline else None)


def _run_progress(run) -> dict:
    """Computed even for terminal runs, so the comparison payload keeps its
    historical scored/total counts."""
    from overbae.api.eval_serializers import compute_run_progress  # noqa: PLC0415

    return compute_run_progress(run)


@extend_schema_view(
    list=extend_schema(summary="List eval samples"),
    retrieve=extend_schema(summary="Get eval sample (trajectory + scores)"),
)
class EvalSampleViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    filterset_class = EvalSampleFilter
    ordering_fields = ["created_at"]
    lookup_field = "id"
    pagination_class = EvalPagination

    def get_serializer_class(self):
        if self.action == "list":
            return EvalSampleListSerializer
        return EvalSampleSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return EvalSample.objects.none()
        qs = EvalSample.objects.filter(
            run__project_id__in=_user_project_ids(self.request.user)
        ).select_related("run", "variant")
        if self.action == "retrieve":
            qs = qs.select_related("run__cell").prefetch_related("scores")
        return qs


@extend_schema_view(
    list=extend_schema(summary="List scores"),
    retrieve=extend_schema(summary="Get score"),
)
class ScoreViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = ScoreSerializer
    filterset_class = ScoreFilter
    ordering_fields = ["created_at", "value"]
    lookup_field = "id"
    pagination_class = EvalPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Score.objects.none()
        return Score.objects.filter(
            project_id__in=_user_project_ids(self.request.user)
        ).select_related("variant", "evaluator")


@extend_schema_view(list=extend_schema(summary="List verdicts (live scoring results)"))
class VerdictViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    """The verdict store's read surface: every score detail (span chips,
    execution sheet, live eval scores) reads rows from here."""

    serializer_class = VerdictSerializer
    filterset_class = VerdictFilter
    ordering_fields = ["created_at", "updated_at", "evaluator_name", "score"]
    # Newest-first so a client keeping the first row per (target, evaluator)
    # gets the latest rescore series.
    ordering = ["-updated_at"]
    pagination_class = EvalPagination

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Verdict.objects.none()
        return Verdict.objects.filter(
            project_id__in=_user_project_ids(self.request.user)
        ).select_related("evaluator")
