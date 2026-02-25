import logging

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Overmind Core"
    debug: bool = False
    secret_key: str = "change-me-in-production"
    proxy_token: str = "default-proxy-token"

    # Postgres (docker-compose service)
    database_url: str = (
        "postgresql+asyncpg://overmind:overmind@postgres:5432/overmind_core"
    )

    # Valkey settings
    valkey_host: str = "valkey"
    valkey_port: int = 6379
    valkey_db: int = 0
    valkey_auth_token: str | None = None

    # Celery settings
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None
    celery_task_serializer: str = "json"
    celery_result_serializer: str = "json"

    overmind_traces_url: str = (
        "http://localhost:8000/api/v1/traces/create-backend-trace"
    )
    frontend_url: str = "http://localhost:5173"
    nerpa_base_url: str = ""

    require_email_verification: bool = False
    send_emails: bool = False

    default_dlp_engine: str = "nerpa"

    # At least one LLM key is required for AI features
    openai_api_key: str = ""
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # API token prefix — distinguishes tokens from different editions.
    # Core standalone uses "ovr_core_"; managed/enterprise overrides to "ovr_".
    api_token_prefix: str = "ovr_core_"

    # AWS (optional — only needed for Bedrock, Textract, Comprehend features)
    aws_region: str = "us-east-1"


settings = Settings()

_current_provider = None
_current_token = None
_is_initialized = False

logger = logging.getLogger(__name__)

proxy_token = settings.proxy_token


def setup_opentelemetry():
    global _current_provider, _current_token, _is_initialized

    provider = TracerProvider()

    exporter = OTLPSpanExporter(
        endpoint=settings.overmind_traces_url,
        headers={"Authorization": f"Bearer {proxy_token}"},
        timeout=30000,
    )

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    _current_provider = provider
    _current_token = proxy_token
    _is_initialized = True

    logger.info("OpenTelemetry initialized successfully.")
