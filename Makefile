.PHONY: help install test lint typecheck fmt run-api run-worker compose-up compose-down \
        docker-build helm-lint helm-template tf-init tf-plan tf-apply tf-destroy clean

PYTHON ?= python3
PIP    ?= $(PYTHON) -m pip
HELM   ?= helm
TF     ?= terraform
COMPOSE ?= docker compose

help:
	@echo "Common targets:"
	@echo "  install        Install Python deps (shared lib + dev extras)"
	@echo "  test           Run pytest"
	@echo "  lint           Run ruff"
	@echo "  typecheck      Run mypy"
	@echo "  run-api        Start FastAPI locally"
	@echo "  run-worker     Start Temporal worker locally"
	@echo "  compose-up     Bring up the full local stack (Temporal+API+Worker)"
	@echo "  compose-down   Tear it down"
	@echo "  docker-build   Build api & worker images"
	@echo "  helm-lint      Lint the Helm chart"
	@echo "  helm-template  Render the chart"
	@echo "  tf-init/plan/apply/destroy  Run Terraform against AWS"

install:
	$(PIP) install -e packages/coding_agent[dev]
	$(PIP) install -r services/api/requirements.txt
	$(PIP) install -r services/worker/requirements.txt

test:
	cd packages/coding_agent && $(PYTHON) -m pytest -q

lint:
	$(PYTHON) -m ruff check packages services

typecheck:
	$(PYTHON) -m mypy packages/coding_agent/src

fmt:
	$(PYTHON) -m ruff format packages services

run-api:
	cd services/api && uvicorn main:app --reload --port 8000

run-worker:
	cd services/worker && $(PYTHON) worker.py

compose-up:
	$(COMPOSE) up --build -d

compose-down:
	$(COMPOSE) down -v

docker-build:
	docker build -f services/api/Dockerfile    -t coding-agent-api:dev    .
	docker build -f services/worker/Dockerfile -t coding-agent-worker:dev .

helm-lint:
	$(HELM) lint deploy/helm/coding-agent

helm-template:
	$(HELM) template coding-agent deploy/helm/coding-agent

tf-init:
	cd infra/terraform && $(TF) init

tf-plan:
	cd infra/terraform && $(TF) plan -var-file=example.tfvars

tf-apply:
	cd infra/terraform && $(TF) apply -var-file=example.tfvars -auto-approve

tf-destroy:
	cd infra/terraform && $(TF) destroy -var-file=example.tfvars -auto-approve

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type d -name .pytest_cache -prune -exec rm -rf {} +
	find . -type d -name .ruff_cache -prune -exec rm -rf {} +
	find . -type d -name .mypy_cache -prune -exec rm -rf {} +
