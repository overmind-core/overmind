import logging
import os
import sys
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

logger = logging.getLogger(__name__)
# No-op in production, where the vars are injected.
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-ittjx5jmh&^k%)8k@ux6h7)6xlz**!bh$7c1o=r4h3tmg$ivnu",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "False") == "True"

# Deployments set their own path so the admin is not at a guessable URL.
ADMIN_URL_PATH = os.environ.get("DJANGO_ADMIN_PATH", "admin").strip("/") + "/"

ALLOWED_HOSTS = [
    h.strip()
    for h in os.environ.get(
        "DJANGO_ALLOWED_HOSTS",
        "overbae.fly.dev,api.overmindlab.ai,overclaw.pages.dev,localhost,127.0.0.1,0.0.0.0,192.168.97.4",
    ).split(",")
    if h.strip()
]
ALLOWED_HOSTS.append("api.overmind-dev.orb.local")
ALLOWED_HOSTS.append("pritam-tunnel.overmindlab.ai")
# Browser `localhost` on macOS often hits the API over IPv6; curl/Host is `[::1]`.
ALLOWED_HOSTS.extend(["::1", "[::1]"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "django_celery_beat",
    "storages",
    "overbae",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

TESTING = "test" in sys.argv or "PYTEST_VERSION" in os.environ

if not DEBUG and not TESTING and not os.environ.get("FIELD_ENCRYPTION_KEY"):
    print("FIELD_ENCRYPTION_KEY must be configured when DJANGO_DEBUG is disabled.")


def show_debug_toolbar(request):
    # OrbStack/Docker: REMOTE_ADDR is the gateway, not 127.0.0.1 — IP checks miss.
    # Use settings.DEBUG (not this module's DEBUG) so pytest stays dark.
    from django.conf import settings as dj_settings

    if not dj_settings.DEBUG:
        return False
    # DJDT on every JSON poll saturates Django's one thread-sensitive ASGI
    # executor; the React app never renders the toolbar anyway.
    path = request.path
    return path != "/health" and not path.startswith("/api/")


if DEBUG and not TESTING:
    INSTALLED_APPS = [*INSTALLED_APPS, "debug_toolbar"]
    MIDDLEWARE = [
        "debug_toolbar.middleware.DebugToolbarMiddleware",
        *MIDDLEWARE,
    ]
    INTERNAL_IPS = ["127.0.0.1", "::1"]
    DEBUG_TOOLBAR_CONFIG = {
        "SHOW_TOOLBAR_CALLBACK": "overbae.settings.show_debug_toolbar",
        "UPDATE_ON_FETCH": True,
        "SQL_WARNING_THRESHOLD": 100,
        "RESULTS_CACHE_SIZE": 50,
    }

CORS_ALLOW_ALL_ORIGINS = DEBUG

CORS_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.environ.get(
        "DJANGO_CORS_ALLOWED_ORIGINS",
        # overmindlab.ai (+ www) is the marketing site: it calls the public
        # /api/public/models/ endpoints client-side for its /library pages.
        "https://console.overmindlab.ai,https://claw.overmindlab.ai,"
        "https://overmindlab.ai,https://www.overmindlab.ai,"
        "http://localhost:5173,http://localhost:3000",
    ).split(",")
    if o.strip()
]

# Not CORS-safelisted, so without this the browser hides it from JS and dataset
# exports lose their server-assigned "<name>@<sha>.jsonl" filename.
CORS_EXPOSE_HEADERS = ["Content-Disposition"]

ROOT_URLCONF = "overbae.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "overbae.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("POSTGRES_DB", "overbae"),
        "USER": os.environ.get("POSTGRES_USER", "overbae"),
        "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "overbae"),
        "HOST": os.environ.get("POSTGRES_HOST", "postgres"),
        "PORT": os.environ.get("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": 0,
        "OPTIONS": {
            "connect_timeout": 10,
            **({"sslmode": "require"} if not DEBUG else {}),
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "static"

MEDIA_URL = "media/"
# Must be a real local path: dataset Parquet files live under it. On ECS this is
# /data/media on the EFS volume.
MEDIA_ROOT = Path(os.environ.get("MEDIA_ROOT", BASE_DIR / "media"))

# The single EFS volume ECS mounts at /data; MEDIA_ROOT lives under it.
DATA_ROOT = Path(os.environ.get("DATA_ROOT", BASE_DIR / "data"))
TMPDIR = str(DATA_ROOT / "tmp")

# The Fargate container root filesystem is read-only, so /data/tmp is the only
# place mkstemp / NamedTemporaryFile can write. Every process that imports
# settings needs this wiring.
import tempfile as _tempfile  # noqa: E402 — intentional late import

os.makedirs(TMPDIR, exist_ok=True)
_tempfile.tempdir = TMPDIR
os.environ.setdefault("TMPDIR", TMPDIR)

# Covers FileField uploads only. Dataset files bypass Django storage and are
# written to MEDIA_ROOT on the local/EFS filesystem.
_S3_BUCKET = os.environ.get("AWS_STORAGE_BUCKET_NAME", "overmind-prod-media-318651457362")
_S3_STATIC_BUCKET = os.environ.get("AWS_STATIC_BUCKET_NAME", "overmind-prod-static-318651457362")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")
_AWS_PROFILE = os.environ.get("AWS_PROFILE", "")
_S3_CUSTOM_DOMAIN = os.environ.get("AWS_S3_CUSTOM_DOMAIN", "static.overmindlab.ai")

_STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3boto3.S3Boto3Storage",
        "OPTIONS": {
            "bucket_name": _S3_BUCKET,
            "region_name": AWS_REGION,
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": 3600,
            "file_overwrite": False,
            **({"session_profile": _AWS_PROFILE} if _AWS_PROFILE else {}),
        },
    },
    "staticfiles": {
        "BACKEND": "storages.backends.s3boto3.S3StaticStorage",
        "OPTIONS": {
            "bucket_name": _S3_STATIC_BUCKET,
            "region_name": AWS_REGION,
            "default_acl": None,
            "querystring_auth": False,
            "file_overwrite": True,
            "custom_domain": _S3_CUSTOM_DOMAIN,
            **({"session_profile": _AWS_PROFILE} if _AWS_PROFILE else {}),
        },
    },
}

# Keep STATIC_URL local under DEBUG so DJDT / admin CSS resolve (CDN has no debug_toolbar).
if not DEBUG:
    if _S3_CUSTOM_DOMAIN:
        STATIC_URL = f"https://{_S3_CUSTOM_DOMAIN}/"
    elif _S3_STATIC_BUCKET:
        STATIC_URL = f"https://{_S3_STATIC_BUCKET}.s3.{AWS_REGION}.amazonaws.com/"

# None disables the 2.5 MB body cap, which would reject large dataset/fix
# payloads before any handler runs; the API layer still applies its own limits.
# FILE_UPLOAD_MAX_MEMORY_SIZE stays at its default on purpose — it is the
# spill-to-disk threshold, not a cap, so bigger files stream to disk.
DATA_UPLOAD_MAX_MEMORY_SIZE = None

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")

# A trace with no root span is "live" until it has been quiet this long, then
# "interrupted" — the root ends last, so a killed run never exports one.
TRACE_SETTLE_SECONDS = int(os.environ.get("TRACE_SETTLE_SECONDS", "600"))

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PRO_PRICE_ID = os.environ.get("STRIPE_PRO_PRICE_ID")
# One-time price of a single Overmind Credit ($0.01); top-up quantity = credits bought.
STRIPE_CREDIT_PRICE_ID = os.environ.get("STRIPE_CREDIT_PRICE_ID")

# The hosted GLiNER endpoint handles unstructured PII (PERSON/LOCATION/ORG);
# structured secrets stay on the local regex/checksum validators.
PII_NER_ENDPOINT_URL = os.environ.get("PII_NER_ENDPOINT_URL", "")
PII_NER_TOKEN = os.environ.get("PII_NER_TOKEN", "")
# Minimum GLiNER confidence, applied at decode by the endpoint so changing it
# needs no redeploy. Calibrated on the medical corpus: 0.80 drops zero-shot false
# positives such as anatomy read as LOCATION while keeping real named entities.
PII_NER_MIN_SCORE = float(os.environ.get("PII_NER_MIN_SCORE", "0.8"))

AUTH_USER_MODEL = "overbae.User"

REST_FRAMEWORK = {
    "EXCEPTION_HANDLER": "overbae.api.exception_handler.api_exception_handler",
    "DEFAULT_PAGINATION_CLASS": "overbae.api.config.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Throttles key on the client address. Behind a load balancer the count of
    # trusted proxies decides which X-Forwarded-For hop that is; unset, DRF trusts
    # the whole header.
    "NUM_PROXIES": int(os.environ["NUM_PROXIES"]) if os.environ.get("NUM_PROXIES") else None,
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
        "rest_framework.renderers.BrowsableAPIRenderer",
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "overbae.auth.ClerkAuthentication",
        "overbae.api.authentication.APITokenBackend",
        "overbae.api.authentication.GuestJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(days=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Overbae API",
    "DESCRIPTION": "Backend API for the Overclaw / Overmind platform.",
    "VERSION": "2.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": r"/api/",
    "ENUM_NAME_OVERRIDES": {
        "IntentEnum": "overbae.models.datasets.Dataset.Intent",
        "CellStateEnum": "overbae.models.datasets.Cell.State",
        # Lane provenance shares the field name with finetuning evidence; pinned so
        # neither enum is hash-renamed in the generated client.
        "ProvenanceEnum": [("measured", "measured"), ("lab_claimed", "lab_claimed")],
        "CapabilityProposalMethodEnum": [
            ("source", "source"),
            ("name", "name"),
            ("capability_card", "capability_card"),
        ],
        "CapabilityStatusEnum": "overbae.models.capabilities.Capability.Status",
        "SpanTypeEnum": "overbae.models.traces.Span.SpanType",
        "BacktestRunStatusEnum": "overbae.models.traces.BacktestRun.RunStatus",
        "FinetuningJobStatusEnum": "overbae.models.finetuning.FinetuningJob.Status",
        "SignOnMethodEnum": "overbae.models.iam.SignOnMethod",
        "ModelRefProviderEnum": "overbae.models.evaluation.ModelRef.Provider",
        "EvaluatorKindEnum": "overbae.models.evaluation.Evaluator.Kind",
        "EvaluatorScopeEnum": "overbae.models.evaluation.Evaluator.Scope",
        # EvalRun.Status is absent on purpose: its members match another status
        # enum and it reuses that component.
        "EvalRunDataSourceEnum": "overbae.models.evaluation.EvalRun.DataSource",
        "EvalVariantModeEnum": "overbae.models.evaluation.EvalVariant.Mode",
        # Evaluator.ScoreType shares members with Score.DataType, so this one
        # override name covers both.
        "ScoreDataTypeEnum": "overbae.models.evaluation.Score.DataType",
        "ScoreFailureRoleEnum": "overbae.models.evaluation.Score.FailureRole",
        "ScoreSourceEnum": "overbae.models.evaluation.Score.Source",
        "DatasetSourceKindEnum": "overbae.models.datasets.Dataset.SourceKind",
    },
    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "ApiKeyAuth": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Api-Key",
                "description": "API key authentication.",
            },
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
        }
    },
    "SECURITY": [{"ApiKeyAuth": []}, {"BearerAuth": []}],
}

# db 1, kept off the Celery broker's db 0. Backs the model-catalog and grounding
# caches and the scoring locks.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("CACHE_URL", "redis://redis:6379/1"),
    }
}

CELERY_BROKER_URL = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "UTC"

# Workers are resource profiles, queues are fairness classes. Only prefork
# enforces time_limit and revoke(terminate=True), so every time-limited task
# routes to a prefork lane; test_celery_topology asserts it. io_traces is a
# second queue on the io worker, not a second worker: round-robin keeps an
# unbounded trace burst from queueing ahead of user-started eval scoring.
CELERY_TASK_DEFAULT_QUEUE = "control"

CELERY_TASK_ROUTES = {
    "overbae.tasks.datasets.run": {"queue": "interactive"},
    "overbae.tasks.datasets.turn": {"queue": "interactive"},
    "overbae.tasks.datasets.diagnose": {"queue": "interactive"},
    "overbae.tasks.eval.prepare_sample": {"queue": "batch"},
    "overbae.tasks.datasets.land": {"queue": "batch"},
    "overbae.tasks.connector_sync.sync_connector_chunk": {"queue": "batch"},
    "overbae.tasks.eval.execute_evaluator": {"queue": "io"},
    "overbae.tasks.connector_sync.poll_connectors": {"queue": "io"},
    "overbae.tasks.connector_sync.sweep_abandoned_drafts": {"queue": "io"},
    "overbae.tasks.capability_rebind.rebind_unbound_spans": {"queue": "io"},
    "overbae.tasks.behaviour.rebind_parked_executions": {"queue": "io"},
    "overbae.tasks.trace_scoring.score_trace": {"queue": "io_traces"},
}

CELERY_BEAT_SCHEDULE = {
    # Observe live trains; re-drive submit/register if the Celery task vanished.
    "reconcile-finetuning-jobs": {
        "task": "overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs",
        "schedule": 15.0,
    },
    # Hourly because Baseten's billing lags the job.
    "sync-baseten-finetuning-costs": {
        "task": "overbae.tasks.baseten_billing_sync.sync_baseten_finetuning_costs",
        "schedule": 3600.0,
    },
    # Reaps runs stuck in RUNNING with no DB progress, i.e. a lost chord barrier.
    "reap-stalled-eval-runs": {
        "task": "overbae.tasks.eval_watchdog.reap_stalled_eval_runs",
        "schedule": 600.0,
    },
    # Reaps notebook runs orphaned by a killed worker.
    "reap-stuck-dataset-runs": {
        "task": "overbae.tasks.datasets.reap_stuck_runs",
        "schedule": 600.0,
    },
    "cleanup-data-tmp": {
        "task": "overbae.tasks.cleanup_tmp.cleanup_data_tmp",
        "schedule": 21600.0,
    },
    "prune-modal-sft-volume": {
        "task": "overbae.tasks.cleanup_modal.prune_modal_sft_volume",
        "schedule": 86400.0,
    },
    "prewarm-base-models": {
        "task": "overbae.tasks.base_models.prewarm_base_models",
        "schedule": 86400.0,
    },
    "sweep-guest-workspaces": {
        "task": "overbae.tasks.guest_cleanup.sweep_guest_workspaces",
        "schedule": 86400.0,
    },
    "reconcile-optimizer-experiments": {
        "task": "overbae.tasks.optimizer_reconciler.reconcile_optimizer_experiments",
        "schedule": 10.0,
    },
    # Fails DeployedModels stuck in QUANTIZING/DEPLOYING for >90 min.
    "janitor-stuck-fsm": {
        "task": "overbae.tasks.inference_controller.janitor_stuck_fsm",
        "schedule": 300.0,
    },
    # Backstop for stragglers whose live scoring enqueue was lost.
    "sweep-unscored-traces": {
        "task": "overbae.tasks.trace_scoring.sweep_unscored_traces",
        "schedule": 120.0,
    },
    # This cadence is only the outer tick: per-credential pacing and backoff are
    # gated by ConnectorCredential.next_poll_at. Kept at 60s so the fastest
    # user-configurable poll_interval_seconds preset (1 min) is meaningful.
    "poll-connectors": {
        "task": "overbae.tasks.connector_sync.poll_connectors",
        "schedule": 60.0,
    },
    "sweep-connector-drafts": {
        "task": "overbae.tasks.connector_sync.sweep_abandoned_drafts",
        "schedule": 3600.0,
    },
}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
CURSOR_API_KEY = os.environ.get("CURSOR_API_KEY", "")

# Feedback-channel incoming webhook; blank disables the notification.
SLACK_FEEDBACK_WEBHOOK_URL = os.environ.get("SLACK_FEEDBACK_WEBHOOK_URL", "")

# Blank disables Clerk sign-in; API keys and guest sessions still authenticate.
CLERK_API_SECRET_KEY = os.environ.get("CLERK_API_SECRET_KEY", "")
CLERK_AUTHORIZED_PARTIES = os.environ.get(
    "CLERK_AUTHORIZED_PARTIES", "http://localhost:5173"
).split(",")

# TLS terminates at the Fly proxy, which forwards X-Forwarded-Proto: trusting it
# is how Django sees a client's HTTPS behind plain HTTP on the machine.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# The proxy already redirects (fly.toml: force_https = true). True here would
# make redirect loops behind it.
SECURE_SSL_REDIRECT = False

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

    SECURE_HSTS_SECONDS = 31_536_000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True

SECURE_CONTENT_TYPE_NOSNIFF = True

SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

X_FRAME_OPTIONS = "DENY"

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {name} {funcName} {lineno:d} {message}",
            "style": "{",
        },
        "json": {
            "()": "pythonjsonlogger.json.JsonFormatter",
            "format": "%(levelname)s %(asctime)s %(message)s %(name)s %(module)s  %(funcName)s %(lineno)d",
        },
    },
    "filters": {
        "skip_health": {
            "()": "django.utils.log.CallbackFilter",
            "callback": lambda record: "GET /health" not in record.getMessage(),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose" if DEBUG else "json",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "WARNING",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "django.security": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
        "django.server": {
            "handlers": ["console"],
            "level": "INFO",
            "filters": ["skip_health"],
            "propagate": False,
        },
        "overbae": {
            "handlers": ["console"],
            "level": os.environ.get("OVERBAE_LOG_LEVEL", "INFO"),
            "propagate": False,
        },
        "uvicorn": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.access": {
            "handlers": ["console"],
            "level": "INFO",
            "filters": ["skip_health"],
            "propagate": False,
        },
    },
}

TOGETHER_API_KEY = os.environ.get("TOGETHER_API_KEY", "")

BASETEN_API_KEY = os.environ.get("BASETEN_API_KEY", "")
BASETEN_PROJECT = os.environ.get("BASETEN_PROJECT", "")

HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Durable home for a fine-tune's checkpoint.zip / job_logs.txt / metrics.json.
# The names match Modal's overmind-inference secret. These static keys go
# straight to boto3.client() in finetuning_checkpoints; the media storage above
# still authenticates with AWS_PROFILE instead.
AWS_BUCKET_NAME = os.environ.get("AWS_BUCKET_NAME", "")
AWS_ACCESS_KEY_ID = os.environ.get("AWS_ACCESS_KEY_ID", "")
AWS_SECRET_ACCESS_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY", "")

# "baseten", "together", or "modal".
FINETUNING_BACKEND = os.environ.get("FINETUNING_BACKEND", "modal")

# Trainer engine, an axis independent of FINETUNING_BACKEND: false picks the
# plain TRL SFTTrainer, true picks Unsloth's FastLanguageModel. All four
# (backend, engine) combinations are supported.
USE_UNSLOTH = os.environ.get("USE_UNSLOTH", "true").strip().lower() in ("1", "true", "yes")

# Must be `modal deploy`-ed before FINETUNING_BACKEND=modal can submit jobs.
MODAL_SFT_APP_NAME = os.environ.get("MODAL_SFT_APP_NAME", "overmind-sft")

_missing_aws = [
    name
    for name, value in (
        ("AWS_BUCKET_NAME", AWS_BUCKET_NAME),
        ("AWS_ACCESS_KEY_ID", AWS_ACCESS_KEY_ID),
        ("AWS_SECRET_ACCESS_KEY", AWS_SECRET_ACCESS_KEY),
    )
    if not value
]
if _missing_aws:
    raise ImproperlyConfigured(
        f"{', '.join(_missing_aws)} must be set (fine-tuning checkpoint S3 archive)"
    )

if FINETUNING_BACKEND == "together" and not TOGETHER_API_KEY:
    raise ImproperlyConfigured("TOGETHER_API_KEY must be set when FINETUNING_BACKEND=together")

if FINETUNING_BACKEND == "baseten" and not BASETEN_API_KEY:
    raise ImproperlyConfigured("BASETEN_API_KEY must be set when FINETUNING_BACKEND=baseten")

if FINETUNING_BACKEND == "modal" and not (
    os.environ.get("MODAL_TOKEN_ID") and os.environ.get("MODAL_TOKEN_SECRET")
):
    raise ImproperlyConfigured(
        "MODAL_TOKEN_ID and MODAL_TOKEN_SECRET must be set when FINETUNING_BACKEND=modal"
    )

# The InferenceAPIServer ASGI endpoint, printed by `modal deploy`.
INFERENCE_API_URL: str = os.environ.get("INFERENCE_API_URL", "")
# Must match INFERENCE_API_KEY in the Modal overmind-inference secret.
INFERENCE_API_KEY: str = os.environ.get("INFERENCE_API_KEY", "")

if not INFERENCE_API_URL:
    raise ImproperlyConfigured("INFERENCE_API_URL must be set")
