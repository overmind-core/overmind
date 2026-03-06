.PHONY: run stop logs migrate revision test lint psql e2e e2e-rerun e2e-clean e2e-reset-db

OPEN_CMD := $(shell command -v xdg-open 2>/dev/null || command -v open 2>/dev/null)

run:
ifdef OPEN_CMD
	@(until curl -sf http://localhost:8000/health >/dev/null 2>&1 && \
	       curl -sf http://localhost:5173 >/dev/null 2>&1; \
	  do sleep 2; done && $(OPEN_CMD) http://localhost:5173) &
endif
	docker compose up --build

run-detached:
	docker compose up --build -d
ifdef OPEN_CMD
	@(until curl -sf http://localhost:8000/health >/dev/null 2>&1 && \
	       curl -sf http://localhost:5173 >/dev/null 2>&1; \
	  do sleep 2; done && $(OPEN_CMD) http://localhost:5173) &
endif

stop:
	docker compose down

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

migrate:
	docker compose exec api alembic upgrade head

revision:
	docker compose exec api alembic revision --autogenerate -m "$(m)"

test:
	docker compose run --rm api sh -c "poetry install --with test && python -m pytest $(test_args)"

lint:
	poetry run ruff check --fix
	poetry run ruff format

psql:
	docker compose exec postgres psql -U overmind -d overmind_core

shell:
	docker compose exec api python -c "import overmind; print('overmind loaded')"

clean:
	docker compose down -v

build-frontend:
	cd frontend && bun install
	cd frontend && bun run build

deploy-frontend: build-frontend
	cd frontend && bun run wrangler pages deploy dist

e2e:
	@DISABLE_PERIODIC_TASKS=true docker compose up -d --force-recreate --no-deps celery-beat
	@rc=0; (cd tests/e2e && poetry run pytest -x -v --tb=short $(test_args)) || rc=$$?; \
		docker compose up -d --force-recreate --no-deps celery-beat; \
		echo ""; echo "Reports: tests/e2e/reports/report.html, tests/e2e/reports/junit.xml"; \
		exit $$rc

e2e-rerun:
	@DISABLE_PERIODIC_TASKS=true docker compose up -d --force-recreate --no-deps celery-beat
	@rc=0; (cd tests/e2e && poetry run pytest --e2e-rerun -x -v --tb=short $(test_args)) || rc=$$?; \
		docker compose up -d --force-recreate --no-deps celery-beat; \
		echo ""; echo "Reports: tests/e2e/reports/report.html, tests/e2e/reports/junit.xml"; \
		exit $$rc

e2e-clean:
	@DISABLE_PERIODIC_TASKS=true docker compose up -d --force-recreate --no-deps celery-beat
	@rc=0; (cd tests/e2e && poetry run pytest --e2e-clean -x -v --tb=short $(test_args)) || rc=$$?; \
		docker compose up -d --force-recreate --no-deps celery-beat; \
		echo ""; echo "Reports: tests/e2e/reports/report.html, tests/e2e/reports/junit.xml"; \
		exit $$rc

e2e-reset-db:
	@echo "Dropping all tables (keeping LLM cache and containers)…"
	docker compose exec postgres psql -U overmind -d overmind_core \
		-c "DROP SCHEMA public CASCADE; CREATE SCHEMA public; GRANT ALL ON SCHEMA public TO overmind;"
	@echo "Re-running migrations…"
	docker compose exec api alembic upgrade head
	@echo "Restarting API + Celery workers (picks up fresh .env)…"
	docker compose restart api celery-worker celery-beat
	@echo "Waiting for API health check…"
	@until curl -sf http://localhost:8000/health >/dev/null 2>&1; do sleep 2; done
	@echo "DB reset complete. Run 'make e2e' to re-run tests from scratch."


generate_api_client:
	docker run --rm -v $(PWD):/workspace openapitools/openapi-generator-cli:v7.19.0 generate \
		-i http://host.docker.internal:8000/openapi.json \
		-g typescript-fetch \
		-o /workspace/frontend/src/api \
		--additional-properties=typescriptThreePlus=true
