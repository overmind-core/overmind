try:
    # The vLLM image does not install Celery. Importing this package there is the worker.
    from .celery import app as celery_app
except ModuleNotFoundError:
    celery_app = None

__all__ = ["celery_app"]
