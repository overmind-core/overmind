FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction

COPY overmind_core/ ./overmind_core/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY tests/ ./tests/

EXPOSE 8000
