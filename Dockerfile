FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml poetry.lock README.md ./
RUN uv pip compile pyproject.toml -o requirements.txt && \
    uv pip install --system --no-cache -r requirements.txt

COPY overmind/ ./overmind/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY tests/ ./tests/

EXPOSE 8000
