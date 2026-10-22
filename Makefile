.PHONY: install test test-db lint run migrate

install:
	pip install --upgrade pip
	pip install -e ".[dev]"

test:
	pytest

test-db:
	pytest tests/storage/test_repositories.py

lint:
	ruff check .

run:
	uvicorn futuris.api.app:app --host 0.0.0.0 --port 8000 --reload

migrate:
	alembic upgrade head