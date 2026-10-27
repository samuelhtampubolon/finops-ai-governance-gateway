.PHONY: install seed test run lint clean docker-build docker-up docker-down help

PYTHON ?= python

help:
	@echo "Available commands:"
	@echo "  make install     - Install all required Python packages"
	@echo "  make seed        - Populate DuckDB with 3,000 multi-tenant telemetry logs"
	@echo "  make test        - Run complete pytest suite verifying cache, routing & security"
	@echo "  make run         - Launch both FastAPI Gateway (8000) and Streamlit UI (8501)"
	@echo "  make docker-up   - Start full environment via docker-compose"
	@echo "  make docker-down - Stop and tear down docker containers"
	@echo "  make clean       - Remove cached bytecode and test artifacts"

install:
	$(PYTHON) -m pip install -r requirements.txt

seed:
	$(PYTHON) src/seed_finops_telemetry.py

test:
	$(PYTHON) -m pytest -v

run:
	$(PYTHON) run_local.py

docker-build:
	docker compose build

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} +
	rm -rf .pytest_cache htmlcov .coverage
