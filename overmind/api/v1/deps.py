from celery import Celery
from fastapi import Request


# Database dependency - use get_db directly with FastAPI's Depends()
# DO NOT create helper functions that call next(get_db()) as this breaks
# the generator pattern and causes connection leaks


def get_celery_app(request: Request) -> Celery:
    return request.app.state.celery_app
