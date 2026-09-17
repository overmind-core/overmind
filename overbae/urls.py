from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)
from rest_framework.permissions import AllowAny
from rest_framework.routers import DefaultRouter

from overbae.api.auth_keys import APITokenCurrentView, APITokenDestroyView, APITokenListCreateView
from overbae.api.auth_registration import LocalSessionView, OnboardingView, UserMeView
from overbae.api.auth_token_views import PublicTokenObtainPairView, PublicTokenRefreshView
from overbae.api.behaviours import BehaviourViewSet, TaskExecutionViewSet
from overbae.api.billing import (
    BillingLedgerView,
    CancelSubscriptionView,
    CheckoutSessionView,
    CreditTopUpView,
    RenewSubscriptionView,
    StripeWebhookView,
    SubscriptionView,
)
from overbae.api.capabilities import AgentGraphView, CapabilityViewSet
from overbae.api.completions import chat_completions, model_detail, models_list
from overbae.api.datasets import DatasetViewSet
from overbae.api.eval_views import (
    EvalRunViewSet,
    EvalSampleViewSet,
    EvalSetViewSet,
    EvaluatorViewSet,
    ModelCatalogView,
    ScoreViewSet,
    VerdictViewSet,
)
from overbae.api.guest import GuestClaimView, GuestStartView
from overbae.api.health import health_check
from overbae.api.optimizer import OptimizerCandidateViewSet, OptimizerExperimentViewSet
from overbae.api.otlp import otlp_traces
from overbae.api.public_models import PublicModelLibraryDetailView, PublicModelLibraryListView
from overbae.api.sync import SyncView
from overbae.api.uploads import UploadViewSet
from overbae.api.views import (
    ConnectorCredentialViewSet,
    DeployedModelViewSet,
    FeedbackViewSet,
    FinetuningJobViewSet,
    ProjectInviteViewSet,
    ProjectMembershipViewSet,
    ProjectViewSet,
    SessionViewSet,
    SpanViewSet,
)

router = DefaultRouter()
# Must precede ``projects`` so ``…/memberships`` is not captured as a project id.
router.register(
    r"projects/(?P<project_id>[0-9a-f-]{36})/memberships",
    ProjectMembershipViewSet,
    basename="project-membership",
)
router.register(
    r"projects/(?P<project_id>[0-9a-f-]{36})/invites",
    ProjectInviteViewSet,
    basename="project-invite",
)
router.register(r"projects", ProjectViewSet, basename="project")
router.register(r"capabilities", CapabilityViewSet, basename="capability")
router.register(r"datasets", DatasetViewSet, basename="dataset")
router.register(r"uploads", UploadViewSet, basename="upload")
# Backed by the Span table: list returns root spans, retrieve returns every span
# sharing the trace_id. There is no separate /spans/.
router.register(r"traces", SpanViewSet, basename="trace")
# Traces grouped by the ``conversation.id`` span attribute.
router.register(r"sessions", SessionViewSet, basename="session")
router.register(r"feedback", FeedbackViewSet, basename="feedback")
router.register(
    r"connector-credentials", ConnectorCredentialViewSet, basename="connectorcredential"
)
router.register(
    r"optimizer-experiments", OptimizerExperimentViewSet, basename="optimizer-experiment"
)
router.register(r"optimizer-candidates", OptimizerCandidateViewSet, basename="optimizer-candidate")
router.register(r"finetuning-jobs", FinetuningJobViewSet, basename="finetuningjob")
router.register(r"deployed-models", DeployedModelViewSet, basename="deployedmodel")
router.register(r"evaluators", EvaluatorViewSet, basename="evaluator")
router.register(r"eval-sets", EvalSetViewSet, basename="evalset")
router.register(r"eval-runs", EvalRunViewSet, basename="evalrun")
router.register(r"eval-samples", EvalSampleViewSet, basename="evalsample")
router.register(r"eval-scores", ScoreViewSet, basename="evalscore")
router.register(r"verdicts", VerdictViewSet, basename="verdict")
router.register(r"behaviours", BehaviourViewSet, basename="behaviour")
router.register(r"task-executions", TaskExecutionViewSet, basename="taskexecution")

urlpatterns = [
    path(settings.ADMIN_URL_PATH, admin.site.urls),
    path("health", health_check, name="health"),
    path("api/v1/traces", otlp_traces, name="otlp-traces"),
    path("v1/traces", otlp_traces, name="otlp-traces-compat"),
    # OpenAI-compatible inference API
    path("api/v1/chat/completions", chat_completions, name="v1-chat-completions"),
    path("api/v1/models", models_list, name="v1-models"),
    path("api/v1/models/<path:model_id>", model_detail, name="v1-model-detail"),
    path("api/", include(router.urls)),
    path("api/agent/", AgentGraphView.as_view(), name="agent-graph"),
    path("api/v1/sync", SyncView.as_view(), name="toml-sync"),
    path("api/models/catalog/", ModelCatalogView.as_view(), name="model-catalog"),
    # Unauthenticated: powers the marketing site's /library pages.
    path(
        "api/public/models/",
        PublicModelLibraryListView.as_view(),
        name="public-model-library-list",
    ),
    path(
        "api/public/models/<str:slug>/",
        PublicModelLibraryDetailView.as_view(),
        name="public-model-library-detail",
    ),
    path("api/auth/api-keys/", APITokenListCreateView.as_view(), name="api-keys-list-create"),
    path("api/auth/api-keys/current/", APITokenCurrentView.as_view(), name="api-keys-current"),
    path("api/auth/api-keys/<uuid:id>/", APITokenDestroyView.as_view(), name="api-keys-destroy"),
    path("api/auth/local/", LocalSessionView.as_view(), name="local-session"),
    path("api/auth/guest/", GuestStartView.as_view(), name="guest-start"),
    path("api/auth/guest/claim/", GuestClaimView.as_view(), name="guest-claim"),
    path("api/auth/me/", UserMeView.as_view(), name="user-me"),
    path("api/auth/onboarding/", OnboardingView.as_view(), name="user-onboarding"),
    path("api/auth/token/", PublicTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/token/refresh/", PublicTokenRefreshView.as_view(), name="token_refresh"),
    path("api/billing/subscription/", SubscriptionView.as_view(), name="billing-subscription"),
    path("api/billing/ledger/", BillingLedgerView.as_view(), name="billing-ledger"),
    path("api/billing/checkout/", CheckoutSessionView.as_view(), name="billing-checkout"),
    path("api/billing/topup/", CreditTopUpView.as_view(), name="billing-topup"),
    path("api/billing/cancel/", CancelSubscriptionView.as_view(), name="billing-cancel"),
    path("api/billing/renew/", RenewSubscriptionView.as_view(), name="billing-renew"),
    path("api/billing/webhook/", StripeWebhookView.as_view(), name="billing-webhook"),
    path("api/schema/", SpectacularAPIView.as_view(permission_classes=[AllowAny]), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(permission_classes=[AllowAny], url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(permission_classes=[AllowAny], url_name="schema"),
        name="redoc",
    ),
]

if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    if not getattr(settings, "TESTING", False):
        from debug_toolbar.toolbar import debug_toolbar_urls

        urlpatterns += debug_toolbar_urls()


def json_server_error(request, *args, **kwargs):
    """Errors outside DRF's dispatch (middleware, non-DRF views) still return JSON."""
    from django.http import JsonResponse

    return JsonResponse(
        {"detail": "The server hit an unexpected error.", "code": "internal_error"},
        status=500,
    )


handler500 = json_server_error
