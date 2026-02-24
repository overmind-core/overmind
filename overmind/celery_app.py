from celery import Celery, signals
from celery.schedules import crontab

from overmind.config import settings


@signals.worker_process_init.connect
def init_worker_process(**kwargs):
    """
    Initialize each worker process after fork.
    This is critical for async database connections with Celery's prefork pool.

    When Celery forks workers, async database engines and event loops from the
    parent process become invalid. We need to reset them so each worker creates
    its own fresh connections.
    """
    import logging

    logger = logging.getLogger(__name__)

    logger.info("Initializing worker process - resetting database connections")

    # Reset global database engine and session to None
    # This forces each worker to create its own async engine on first use
    import overmind.db.session as session_module

    session_module._engine = None
    session_module._AsyncSessionLocal = None

    logger.info("Worker process initialized successfully")


@signals.worker_process_shutdown.connect
def shutdown_worker_process(**kwargs):
    """
    Clean up resources when worker process shuts down.
    Properly dispose of async database connections.
    """
    import logging
    import asyncio

    logger = logging.getLogger(__name__)
    logger.info("Shutting down worker process - disposing database connections")

    import overmind.db.session as session_module

    # Dispose of the async engine if it exists
    if session_module._engine is not None:
        try:
            # Run the async dispose in a new event loop
            asyncio.run(session_module.dispose_engine())
        except Exception as e:
            logger.error(f"Error disposing database engine during shutdown: {e}")

    logger.info("Worker process shutdown complete")


def _build_broker_url() -> str:
    if settings.celery_broker_url:
        return settings.celery_broker_url

    scheme = "rediss" if settings.valkey_auth_token else "redis"
    auth_segment = (
        f":{settings.valkey_auth_token}@" if settings.valkey_auth_token else ""
    )
    ssl_params = "?ssl_cert_reqs=CERT_REQUIRED" if settings.valkey_auth_token else ""
    return f"{scheme}://{auth_segment}{settings.valkey_host}:{settings.valkey_port}/{settings.valkey_db}{ssl_params}"


def _build_result_backend() -> str:
    if settings.celery_result_backend:
        return settings.celery_result_backend
    return _build_broker_url()


celery_app = Celery(
    "overmind",
    broker=_build_broker_url(),
    backend=_build_result_backend(),
)

# Configure SSL for broker and backend when using rediss:// (production with auth token)
_ssl_conf = {}
if settings.valkey_auth_token:
    import ssl

    _ssl_conf = {
        "broker_use_ssl": {"ssl_cert_reqs": ssl.CERT_REQUIRED},
        "redis_backend_use_ssl": {"ssl_cert_reqs": ssl.CERT_REQUIRED},
    }

celery_app.conf.update(
    task_serializer=settings.celery_task_serializer,
    result_serializer=settings.celery_result_serializer,
    timezone="UTC",
    enable_utc=True,
    **_ssl_conf,
    beat_schedule={
        "agent-discovery": {
            "task": "agent_discovery.discover_agents",
            "schedule": 20.0,  # Every 5 minutes (300 seconds)
        },
        "auto-evaluate-unscored-spans": {
            "task": "auto_evaluation.evaluate_unscored_spans",
            "schedule": 20.0,  # Every 5 minutes (300 seconds)
        },
        "prompt-improvement": {
            "task": "prompt_improvement.improve_prompt_templates",
            "schedule": 300.0,  # Every 5 minutes (300 seconds)
        },
        "model-backtesting": {
            "task": "backtesting.check_backtesting_candidates",
            "schedule": 300.0,  # Every 5 minutes (300 seconds)
        },
        "job-reconciler": {
            "task": "job_reconciler.reconcile_pending_jobs",
            "schedule": 30.0,  # Every 30 seconds
        },
        "job-cleanup": {
            "task": "job_cleanup.cleanup_old_jobs",
            "schedule": crontab(hour=0, minute=0),  # Daily at midnight UTC
        },
        "periodic-review-triggers": {
            "task": "periodic_reviews.check_review_triggers",
            "schedule": 20.0,  # Every hour (3600 seconds)
        },
    },
)

celery_app.autodiscover_tasks(["overmind.tasks"])


def get_celery_app() -> Celery:
    return celery_app
