# Database migrations. DATABASE_URL comes from the environment, or from ../.env.local if present.
ENV_FILE := $(wildcard ../.env.local)
UV_RUN := uv run $(if $(ENV_FILE),--env-file $(ENV_FILE))

.PHONY: migrate migrate-down migration db-current openapi

openapi:  ## write the route contract to ../shared/openapi.json (no database needed)
	uv run python scripts/export_openapi.py

migrate:  ## apply all migrations
	$(UV_RUN) alembic upgrade head

migrate-down:  ## roll back one migration
	$(UV_RUN) alembic downgrade -1

migration:  ## new revision: make migration m="add foo"
	$(UV_RUN) alembic revision --autogenerate -m "$(m)"

db-current:  ## show the applied revision
	$(UV_RUN) alembic current
