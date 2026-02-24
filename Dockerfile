FROM oven/bun:1 AS frontend-build

WORKDIR /frontend
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile
COPY frontend/ ./
ENV VITE_API_BASE_URL=""
ENV VITE_PUBLIC_POSTHOG_KEY=""
ENV VITE_PUBLIC_POSTHOG_HOST=""
RUN bun run build

FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry && \
    poetry config virtualenvs.create false && \
    poetry install --no-root --no-interaction

COPY overmind/ ./overmind/
COPY alembic/ ./alembic/
COPY alembic.ini ./alembic.ini
COPY tests/ ./tests/

COPY --from=frontend-build /frontend/dist ./frontend_dist

EXPOSE 8000
