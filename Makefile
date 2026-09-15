.PHONY: test test-serial lint-format lint-backend lint-frontend install-hooks psql worker check-migrations

install-hooks:
	uv run pre-commit install

# Local celery worker (macOS). --pool=solo because forking after the httpx Modal
# warmup copies locked mDNS/getaddrinfo state into the child and SIGSEGVs. Solo
# is concurrency 1, so a long workshop turn blocks everything else locally.
# -Q lists every queue: this one process stands in for the whole compose fleet.
worker:
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES TOGETHER_API_KEY=placeholder-local-dev \
		uv run celery -A overbae worker -l info --pool=solo -n local@%h \
		-Q control,io,io_traces,batch,interactive

lint-format:
	$(MAKE) --no-print-directory -j2 lint-backend lint-frontend

lint-backend:
	uv run ruff check --fix
	uv run ruff format

lint-frontend:
	bun run --cwd frontend biome check --fix
	bun run --cwd frontend typecheck

psql:
	docker compose exec postgres psql -U overmind -d overmind_core

# CI passes MIGRATIONS_BASE=HEAD^1 (the merge commit's base); locally, origin/main.
MIGRATIONS_BASE ?= origin/main

check-migrations:
	DJANGO_SETTINGS_MODULE=tests.settings uv run python manage.py makemigrations --check --dry-run
	uv run python scripts/check_migrations.py $(MIGRATIONS_BASE)

clean:
	docker compose down -v

FRONTEND_MODE ?= production

build-frontend:
	cd frontend && bun install
	cd frontend && bun run build --mode $(FRONTEND_MODE)

update-frontend-version:
	@echo "Fetching latest overmind version from PyPi..."
	@latest_version=$$(curl -s https://pypi.org/pypi/overmind/json | jq -r '.info.version'); \
	for file in frontend/.env* ; do \
		if grep -q "^VITE_LATEST_CLI_VERSION=" $$file ; then \
			sed -i.bak "s/^VITE_LATEST_CLI_VERSION=.*/VITE_LATEST_CLI_VERSION=$${latest_version}/" $$file; \
		else \
			echo "VITE_LATEST_CLI_VERSION=$${latest_version}" >> $$file; \
		fi ; \
		rm -f "$$file.bak"; \
		echo "Set VITE_LATEST_CLI_VERSION=$${latest_version} in $$file"; \
	done

deploy-frontend: update-frontend-version build-frontend
	cd frontend && bun run wrangler pages deploy dist $(if $(PAGES_PROJECT),--project-name=$(PAGES_PROJECT),) $(if $(PAGES_BRANCH),--branch=$(PAGES_BRANCH),)

schema:
	DJANGO_DEBUG=True uv run python manage.py spectacular --file openapi.yaml --validate

generate_api_client: schema
	rm -rf frontend/src/openapi/docs frontend/src/openapi/models frontend/src/openapi/apis
	docker run --rm --user $$(id -u):$$(id -g) -e HOME=/tmp -v $(PWD):/workspace \
		openapitools/openapi-generator-cli:v7.19.0 generate \
		-i /workspace/openapi.yaml \
		-g typescript-fetch \
		-o /workspace/frontend/src/openapi \
		--additional-properties=typescriptThreePlus=true,supportsES6=true,enumPropertyNaming=original
	python3 -c "import pathlib; [p.write_text('\n'.join(l.rstrip(' \t') for l in p.read_text().split('\n')).rstrip('\n') + '\n') for p in pathlib.Path('frontend/src/openapi').rglob('*.ts')]"
	rm openapi.yaml

# Number of parallel workers (auto = number of CPUs)
WORKERS ?= auto
test_args ?=

test: ## Run all tests in parallel (default)
	uv run pytest tests/ -n $(WORKERS) --dist worksteal -q $(test_args)

test-serial: ## Run all tests serially (for debugging)
	uv run pytest tests/ -v $(test_args)
