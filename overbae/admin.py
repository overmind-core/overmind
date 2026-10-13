from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from overbae.models import (
    APIToken,
    BillingTelemetry,
    Capability,
    Cell,
    Dataset,
    EvalMatrix,
    EvalRun,
    EvalRunRow,
    EvalScore,
    EvalSet,
    Feedback,
    ModelRef,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
    ProjectMembership,
    SignalEvent,
    SignalSubscription,
    Span,
    Subscription,
    User,
    UserOnboarding,
)


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


@admin.register(EvalMatrix)
class EvalMatrixAdmin(admin.ModelAdmin):
    list_display = ["name", "type", "project", "capability", "span_type", "enabled", "created_at"]
    list_filter = ["type", "enabled", "span_type"]
    search_fields = ["name", "source", "project__slug"]
    readonly_fields = ["id", "created_at", "updated_at"]


@admin.register(EvalSet)
class EvalSetAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "capability", "created_at"]
    search_fields = ["name", "project__slug"]
    filter_horizontal = ["rows"]
    readonly_fields = ["id", "created_at", "updated_at"]


class EvalRunRowInline(admin.TabularInline):
    model = EvalRunRow
    extra = 0
    fields = ["row_index", "state", "degraded", "error"]
    readonly_fields = fields


@admin.register(EvalRun)
class EvalRunAdmin(admin.ModelAdmin):
    list_display = ["name", "project", "status", "mode", "is_baseline", "group", "created_at"]
    list_filter = ["status", "mode", "created_at"]
    search_fields = ["name", "project__slug", "group"]
    readonly_fields = ["id", "created_at", "updated_at", "completed_at"]
    inlines = [EvalRunRowInline]


@admin.register(EvalScore)
class EvalScoreAdmin(admin.ModelAdmin):
    list_display = ["source_type", "source_id", "score", "run", "project", "created_at"]
    list_filter = ["source_type", "created_at"]
    search_fields = ["source_id", "run__name"]
    readonly_fields = ["id", "created_at"]


@admin.register(SignalSubscription)
class SignalSubscriptionAdmin(admin.ModelAdmin):
    list_display = ["signal_type", "project", "capability", "enabled", "created_at"]
    list_filter = ["signal_type", "enabled"]
    search_fields = ["signal_type", "project__slug"]


@admin.register(SignalEvent)
class SignalEventAdmin(admin.ModelAdmin):
    list_display = ["signal_type", "project", "trace_id", "occurred_at", "seen_at"]
    list_filter = ["signal_type", "occurred_at"]
    search_fields = ["signal_type", "trace_id", "message"]
    readonly_fields = ["id", "occurred_at"]
