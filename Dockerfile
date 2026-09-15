# Build from the repository root:
#   docker build -t overbae .

ARG PYTHON_IMAGE=python:3.13-slim-bookworm
ARG UV_VERSION=0.11.24
ARG GIT_VERSION=2.54.0

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- deps: compile wheels, build git (not shipped) ---
FROM ${PYTHON_IMAGE} AS deps

ARG GIT_VERSION

WORKDIR /code

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        build-essential libpq-dev ca-certificates curl \
        libcurl4-openssl-dev libexpat1-dev libssl-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://mirrors.edge.kernel.org/pub/software/scm/git/git-${GIT_VERSION}.tar.xz" \
        | tar -xJ \
    && cd "git-${GIT_VERSION}" \
    && make prefix=/opt/git NO_PERL=YesPlease NO_TCLTK=YesPlease NO_GETTEXT=YesPlease -j"$(nproc)" all \
    && make NO_PERL=YesPlease NO_TCLTK=YesPlease NO_GETTEXT=YesPlease prefix=/opt/git install \
    && cd .. && rm -rf "git-${GIT_VERSION}"

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_SYSTEM_PYTHON=1

COPY pyproject.toml uv.lock README.md ./
RUN mkdir -p overbae modal_shared \
    && touch overbae/__init__.py modal_shared/__init__.py

RUN uv sync --frozen --no-dev --no-editable

# --- runtime ---
FROM ${PYTHON_IMAGE} AS runtime

WORKDIR /code

# Runtime libs for psycopg2-binary and the NO_PERL git binary (no curl/perl apt packages).
# Bump this date to bust the build cache and force a fresh apt security refresh
# whenever ECR/Vanta reports new Debian package CVEs.
ARG APT_SECURITY_REFRESH=2026-07-02

RUN echo "apt security refresh: ${APT_SECURITY_REFRESH}" \
    && apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends \
        libpq5 ca-certificates libcurl4 libssl3 zlib1g libexpat1 \
    && apt-get install -y --only-upgrade \
        libssh2-1 perl-base libcurl4 libexpat1 libacl1 libattr1 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=overbae.settings \
    PATH="/opt/git/bin:/code/.venv/bin:$PATH" \
    VIRTUAL_ENV="/code/.venv" \
    PIP_NO_CACHE_DIR=1

COPY --from=deps /opt/git /opt/git
COPY --from=deps /code/.venv /code/.venv
COPY --from=uv /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock README.md ./
COPY overbae ./overbae/
COPY modal_shared ./modal_shared/
COPY manage.py ./
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY docker/worker_entrypoint.sh /usr/local/bin/worker-entrypoint.sh

RUN uv pip install --python /code/.venv --no-deps . \
    && chmod +x /usr/local/bin/docker-entrypoint.sh \
    && chmod +x /usr/local/bin/worker-entrypoint.sh \
    && rm /usr/local/bin/uv \
    && git --version

# truss/truss_train is a pre-release that uv can't lock; install separately.
# Reinstall watchfiles afterward to avoid truss downgrading it (uvicorn reload needs >=0.21).
RUN python -m ensurepip --upgrade \
    && python -m pip install truss --quiet \
    && python -m pip install "watchfiles>=0.21" --quiet

RUN python manage.py collectstatic --noinput 2>/dev/null || true

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["gunicorn", "overbae.asgi:application", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "2", "--bind", "0.0.0.0:8000", "--timeout", "120", "--graceful-timeout", "30", "--access-logfile", "-"]
