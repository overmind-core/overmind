.PHONY: run stop logs migrate revision test lint psql

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
	docker compose run --rm api python -m pytest $(test_args)

lint:
	poetry run ruff check --fix overmind/
	poetry run ruff format overmind/

psql:
	docker compose exec postgres psql -U overmind -d overmind_core

shell:
	docker compose exec api python -c "import overmind; print('overmind loaded')"

clean:
	docker compose down -v
