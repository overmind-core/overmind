from django.db.models import Case, F, Func, IntegerField, Q, Value, When
from django.db.models.functions import Coalesce, NullIf

from overbae.models import EvalRun, NativeEvaluationPlan


def activity_sources(project_ids, filters):
    runs = EvalRun.objects.filter(project_id__in=project_ids, project_id=filters["project"])
    plans = NativeEvaluationPlan.objects.filter(
        project_id__in=project_ids, project_id=filters["project"], deleted_at=None
    )
    if filters.get("status"):
        runs = runs.filter(status=filters["status"])
        plans = plans.filter(state=filters["status"])
    if filters.get("search"):
        runs = runs.filter(
            Q(name__icontains=filters["search"]) | Q(description__icontains=filters["search"])
        )
        plans = plans.filter(name__icontains=filters["search"])
    if filters.get("dataset"):
        runs = runs.filter(dataset_id=filters["dataset"])
        plans = plans.filter(final_cell__dataset_id=filters["dataset"])
    if filters.get("capability"):
        runs = runs.filter(dataset__capability_id=filters["capability"])
        plans = plans.filter(final_cell__dataset__capability_id=filters["capability"])
    return runs, plans


def evaluation_activity(project_ids, filters):
    runs, plans = activity_sources(project_ids, filters)
    fields = (
        "id",
        "name",
        "status",
        "created_at",
        "kind",
        "capability_name",
        "dataset_name",
        "variant_count",
        "evaluator_count",
    )
    ordering = filters.get("ordering", "-created_at")
    direction = F(ordering.lstrip("-"))
    return (
        runs.order_by()
        .annotate(
            kind=Value("generative"),
            capability_name=F("dataset__capability__name"),
            dataset_name=F("dataset__name"),
            variant_count=Value(1),
            evaluator_count=Func(
                F("matrix"), function="jsonb_array_length", output_field=IntegerField()
            ),
        )
        .values(*fields)
        .union(
            plans.order_by()
            .annotate(
                kind=Value("decision"),
                status=F("state"),
                capability_name=F("final_cell__dataset__capability__name"),
                dataset_name=F("final_cell__dataset__name"),
                variant_count=Coalesce(
                    NullIf(
                        Func(
                            F("config__participants"),
                            function="jsonb_array_length",
                            output_field=IntegerField(),
                        ),
                        Value(0),
                    ),
                    Case(When(job__isnull=False, then=Value(2)), default=Value(0)),
                ),
                evaluator_count=Value(None, output_field=IntegerField()),
            )
            .values(*fields),
            all=True,
        )
        .order_by(
            direction.desc(nulls_last=True)
            if ordering.startswith("-")
            else direction.asc(nulls_last=True),
            "id",
            "kind",
        )
    )


def activity_records(page):
    runs = EvalRun.objects.filter(id__in=[r["id"] for r in page if r["kind"] == "generative"])
    plans = NativeEvaluationPlan.objects.filter(
        id__in=[r["id"] for r in page if r["kind"] == "decision"], deleted_at=None
    )
    records = {("generative", run.id): run for run in runs.select_related("dataset__capability")}
    records.update(
        {
            ("decision", plan.id): plan
            for plan in plans.select_related("final_cell__dataset__capability")
        }
    )
    return [records[(r["kind"], r["id"])] for r in page if (r["kind"], r["id"]) in records]


def activity_datasets(project_ids, filters):
    runs, plans = activity_sources(project_ids, filters)
    pairs = (
        runs.exclude(dataset=None)
        .order_by()
        .values_list("dataset_id", "dataset__name")
        .union(plans.order_by().values_list("final_cell__dataset_id", "final_cell__dataset__name"))
        .order_by("dataset__name")
    )
    return [{"id": identity, "name": name} for identity, name in pairs]
