.PHONY: test test-parallel test-verbose test-cov test-fast test-serial lint-check lint-format help

# Python interpreter — prefers project venv, otherwise system python3
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

# When `uv` is available, run pytest/ruff with the matching groups so
# `make test` works without a pre-synced venv (use: uv sync --extra tracing --group dev).
UV := $(shell command -v uv 2>/dev/null)
ifneq ($(UV),)
PYTEST = $(UV) run --extra tracing --group dev python -m pytest
RUFF = $(UV) run --group dev python -m ruff
else
PYTEST = $(PYTHON) -m pytest
RUFF = $(PYTHON) -m ruff
endif

# Number of parallel workers (auto = number of CPUs)
WORKERS ?= auto

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

test: ## Run all tests in parallel (default)
	$(PYTEST) tests/ -x -n $(WORKERS) --ignore=tests/test_spans.py --dist worksteal -q $(test_args)

test-cov: ## Run all tests with coverage report (sorted by coverage %)
	$(PYTEST) tests/ -n $(WORKERS) --ignore=tests/test_spans.py --dist worksteal --cov=overmind --cov-report=term-missing -q $(test_args)

test-serial: ## Run all tests serially (for debugging)
	$(PYTEST) tests/ -x --ignore=tests/test_spans.py -v $(test_args)

lint-check: ## Check lint + format (CI — no changes)
	$(RUFF) check
	$(RUFF) format --check

lint-format: ## Run ruff linter + formatter (auto-fix)
	$(RUFF) check --fix
	$(RUFF) format
