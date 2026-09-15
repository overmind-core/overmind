from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from overbae.models import (
    Annotation,
    APIToken,
    Behaviour,
    BehaviourVersion,
    BillingTelemetry,
    Capability,
    Cell,
    Dataset,
    EvalRun,
    EvalSample,
    Evaluator,
    EvalVariant,
    Feedback,
    JudgeCache,
    ModelRef,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    RunEvaluator,
    Score,
    Span,
    Subscription,
    TaskExecution,
    User,
    UserOnboarding,
)


@admin.register(Behaviour)
class BehaviourAdmin(admin.ModelAdmin):
    list_display = ["id", "capability", "key", "entry_anchor", "status", "last_seen_sha"]
    list_filter = ["status"]
    search_fields = ["key", "entry_anchor", "capability__name"]


@admin.register(BehaviourVersion)
class BehaviourVersionAdmin(admin.ModelAdmin):
    list_display = ["id", "behaviour", "analyzed_sha", "created_at"]
    search_fields = ["analyzed_sha", "behaviour__key"]


@admin.register(TaskExecution)
class TaskExecutionAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "capability",
        "behaviour",
        "binding_source",
        "success_score",
        "terminal_kind",
        "started_at",
    ]
    list_filter = ["binding_source", "status"]
    search_fields = ["trace_id", "unit_span_id"]


@admin.register(Dataset)
@admin.register(Cell)
class DatasetAdmin(admin.ModelAdmin): ...


@admin.register(OptimizerExperiment)
class OptimizerExperimentAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "capability",
        "status",
        "current_iteration",
        "num_iterations",
        "created_at",
    ]
    list_filter = ["status"]
    search_fields = ["id", "capability__name"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(OptimizerIteration)
class OptimizerIterationAdmin(admin.ModelAdmin):
    list_display = ["id", "experiment", "order", "name", "status", "created_at"]
    list_filter = ["status"]
    search_fields = ["id", "experiment__id"]
    readonly_fields = ["id", "created_at"]


@admin.register(OptimizerCandidate)
class OptimizerCandidateAdmin(admin.ModelAdmin):
    list_display = [
        "id",
        "iteration",
        "candidate_index",
        "is_baseline",
        "score",
        "status",
        "created_at",
    ]
    list_filter = ["status", "is_baseline"]
    search_fields = ["id", "iteration__id"]
    readonly_fields = ["id", "created_at"]
    actions = ["mark_evaluated"]

    @admin.action(description="Mark candidates as evaluated.")
    def mark_evaluated(self, request, queryset):
        updated = queryset.update(status=OptimizerCandidate.Status.EVALUATED)
        self.message_user(request, f"{updated} candidate(s) set to evaluated.")


@admin.register(OptimizerCommand)
class OptimizerCommandAdmin(admin.ModelAdmin):
    actions = ["mark_pending"]
    list_display = [
        "id",
        "iteration",
        "datapoint_index",
        "status",
        "score",
        "created_at",
    ]
    search_fields = [
        "id",
        "experiment__id",
        "candidate__id",
        "iteration__id",
        "original_trace_id",
        "command",
        "code_path",
        "error",
    ]
    list_filter = [
        "experiment",
        "candidate",
        "iteration",
        "status",
        "trace_type",
        "timeout",
        "created_at",
    ]
    readonly_fields = [
        "id",
        "created_at",
        "updated_at",
    ]
    fieldsets = (
        (
            "Identifiers",
            {
                "fields": [
                    "id",
                    "experiment",
                    "candidate",
                    "iteration",
                    "datapoint_index",
                ]
            },
        ),
        (
            "Command Details",
            {
                "fields": [
                    "command",
                    "code_path",
                    "trace_type",
                    "original_trace_id",
                    "timeout",
                    "status",
                ]
            },
        ),
        (
            "Results",
            {
                "fields": [
                    "input",
                    "output",
                    "result",
                    "score",
                    "error",
                ]
            },
        ),
        (
            "Timestamps",
            {
                "fields": [
                    "created_at",
                    "updated_at",
                ]
            },
        ),
    )

    @admin.action(description="Change the status of all selected commands to pending.")
    def mark_pending(self, request, queryset):
        updated = queryset.update(status=OptimizerCommand.Status.PENDING)
        self.message_user(request, f"{updated} command(s) set to pending.")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = (
        "email",
        "is_staff",
        "is_active",
        "email_verified",
        "sign_on_method",
        "date_joined",
        "clerk_user_id",
        "stripe_customer_id",
    )
    list_filter = ("is_staff", "is_superuser", "is_active", "email_verified", "sign_on_method")
    search_fields = ("email", "stripe_customer_id", "clerk_user_id")
    readonly_fields = ("last_login", "date_joined")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "email_verified",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        (
            "Profile",
            {
                "fields": (
                    "sign_on_method",
                    "avatar_url",
                    "timezone",
                    "clerk_user_id",
                    "stripe_customer_id",
                    "projects_limit",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("email", "password1", "password2")}),)


@admin.register(APIToken)
class APITokenAdmin(admin.ModelAdmin):
    list_display = ["prefix", "user", "name", "project", "is_active", "last_used_at", "created_at"]
    list_filter = ["is_active", "created_at"]
    search_fields = ["name", "prefix", "user__email"]
    readonly_fields = ["id", "prefix", "token_hash", "created_at"]


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = [
        "user",
        "status",
        "cancel_at_period_end",
        "start_date",
        "end_date",
        "stripe_subscription_id",
        "updated_at",
    ]
    list_filter = ["status", "cancel_at_period_end"]
    search_fields = [
        "user__email",
        "user__stripe_customer_id",
        "stripe_subscription_id",
        "stripe_price_id",
        "last_stripe_invoice_id",
    ]
    readonly_fields = ["id", "created_at", "updated_at"]
    raw_id_fields = ["user"]


@admin.register(BillingTelemetry)
class BillingTelemetryAdmin(admin.ModelAdmin):
    list_display = ["user", "service", "amount", "project", "timestamp", "idempotency_key"]
    list_filter = ["service"]
    search_fields = ["user__email", "idempotency_key"]
    readonly_fields = ["id", "timestamp"]
    raw_id_fields = ["user", "project"]


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["name", "slug", "is_active", "created_at"]
    list_filter = ["is_active"]
    search_fields = ["name", "slug"]
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ProjectMembership)
class ProjectMembershipAdmin(admin.ModelAdmin):
    list_display = ["user", "project", "created_at"]


@admin.register(Capability)
class CapabilityAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "slug",
        "project",
        "status",
        "observed",
        "model",
        "created_at",
    ]
    list_filter = ["project", "status", "observed"]
    search_fields = ["name", "slug", "description"]
    prepopulated_fields = {"slug": ("name",)}
    raw_id_fields = ["active_eval_set", "active_model"]
    fieldsets = (
        (
            None,
            {
                "fields": (
                    "project",
                    "name",
                    "slug",
                    "description",
                    "status",
                    "observed",
                    "cli_version",
                )
            },
        ),
        (
            "Source",
            {"fields": ("source_path", "entrypoint_fn", "model", "analyzer_model", "footprint")},
        ),
        (
            "Eval Spec",
            {
                "fields": (
                    "input_schema",
                    "output_fields",
                    "output_schema",
                    "structure_weight",
                    "total_points",
                    "tool_config",
                    "tool_usage_weight",
                    "consistency_rules",
                    "optimizable_elements",
                    "fixed_elements",
                    "policy_markdown",
                )
            },
        ),
        (
            "Attachments",
            {
                "fields": (
                    "active_eval_set",
                    "active_model",
                )
            },
        ),
        ("Scan payload", {"fields": ("improvement_metadata", "usage_stats", "last_activity_at")}),
    )


@admin.register(Span)
class SpanAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "span_type",
        "operation",
        "service_name",
        "trace_id",
        "span_id",
        "capability_id",
        "project_id",
        "parent_span_id",
        "kind",
        "status_code",
        "duration_ns",
        "received_at",
    ]
    list_filter = ["project", "span_type", "service_name", "kind", "status_code"]
    search_fields = ["name", "operation", "trace_id", "span_id", "service_name", "parent_span_id"]
    readonly_fields = ["span_id", "received_at"]


@admin.register(UserOnboarding)
class UserOnboardingAdmin(admin.ModelAdmin):
    list_display = ["user", "step", "status", "created_at"]


@admin.register(Feedback)
class FeedbackAdmin(admin.ModelAdmin):
    list_display = ["user", "category", "short_feedback", "read", "created_at"]
    list_filter = ["category", "read", "created_at"]
    search_fields = ["user__email", "feedback"]
    readonly_fields = ["id", "user", "category", "feedback", "created_at"]
    list_display_links = ["short_feedback"]

    @admin.display(description="Feedback")
    def short_feedback(self, obj):
        return obj.feedback[:80] + ("…" if len(obj.feedback) > 80 else "")

    def has_add_permission(self, request):
        return False


@admin.register(ModelRef)
class ModelRefAdmin(admin.ModelAdmin):
    list_display = ["label", "project", "provider", "model_id", "finetuning_job", "created_at"]
    list_filter = ["provider", "created_at"]
    search_fields = ["label", "model_id", "project__slug"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(Evaluator)
class EvaluatorAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "version",
        "kind",
        "scope",
        "project",
        "is_managed",
        "created_at",
        "score_min",
        "score_max",
    ]
    list_filter = ["kind", "scope", "is_managed", "is_archived"]
    search_fields = ["name", "description", "project__slug"]
    readonly_fields = ["id", "created_at"]


class EvalVariantInline(admin.TabularInline):
    model = EvalVariant
    extra = 0
    show_change_link = True


class RunEvaluatorInline(admin.TabularInline):
    model = RunEvaluator
    extra = 0
    show_change_link = True
    fields = ["evaluator", "scope_override", "sampling", "enabled", "order"]


@admin.register(EvalRun)
class EvalRunAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "status", "data_source", "created_at", "completed_at"]
    list_filter = ["status", "data_source", "created_at"]
    search_fields = ["name", "project__slug"]
    readonly_fields = ["id", "created_at", "updated_at", "completed_at"]
    inlines = [EvalVariantInline, RunEvaluatorInline]


@admin.register(EvalSample)
class EvalSampleAdmin(admin.ModelAdmin):
    list_display = ["id", "run", "variant", "source_trace_id", "context_coverage", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["id", "source_trace_id", "run__name"]
    readonly_fields = ["id", "created_at"]


@admin.register(Score)
class ScoreAdmin(admin.ModelAdmin):
    list_display = [
        "name",
        "data_type",
        "value",
        "string_value",
        "passed",
        "scope",
        "failure_role",
        "source",
        "created_at",
    ]
    list_filter = ["data_type", "scope", "failure_role", "source", "created_at"]
    search_fields = ["name", "run__name", "sample__id"]
    readonly_fields = ["id", "created_at"]


@admin.register(Annotation)
class AnnotationAdmin(admin.ModelAdmin):
    list_display = ["id", "sample", "evaluator", "user", "value", "label", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["sample__id", "user__email"]
    readonly_fields = ["id", "created_at"]


@admin.register(JudgeCache)
class JudgeCacheAdmin(admin.ModelAdmin):
    list_display = [
        "key",
        "hits",
        "prompt_tokens",
        "completion_tokens",
        "created_at",
        "last_used_at",
    ]
    search_fields = ["key"]
    readonly_fields = ["key", "raw", "created_at", "last_used_at"]
