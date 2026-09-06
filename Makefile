# Verity -- development tasks.
#
# Everything here runs with no paid service, no API key and no model provider.
# Verification is deterministic; there is nothing to configure.

PY ?= python3
PKGS := packages/schema:packages/evidence:packages/connectors:packages/extract:packages/verifier:packages/cli:apps/sandbox
export PYTHONPATH := $(PKGS):.

CONTRACT ?= examples/contracts/invoice_to_po.yaml
INPUTS := --input invoice_number=INV-4471 --input po_number=PO-2211

.DEFAULT_GOAL := help
.PHONY: help install test lint format typecheck imports check sandbox verify demo quickstart clean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install Verity and its development dependencies
	$(PY) -m pip install -e ".[dev,sandbox,extract]"

test: ## Run the whole test suite
	$(PY) -m pytest -q -p no:warnings

lint: ## Check formatting and lint rules
	$(PY) -m ruff check .

format: ## Apply automatic lint fixes
	$(PY) -m ruff check --fix .

typecheck: ## Strict type checking
	$(PY) -m mypy packages apps

imports: ## Enforce the architectural boundaries (verifier must not reach an executor)
	lint-imports --config .importlinter

check: lint typecheck imports test ## Everything CI runs

sandbox: ## Run the AP sandbox in the foreground on :8099
	$(PY) -m uvicorn verity_sandbox.app:app --host 127.0.0.1 --port 8099 --reload

verify: ## Verify the reference contract against a freshly started sandbox
	./scripts/with-sandbox.sh $(PY) -m verity_cli.main verify --contract $(CONTRACT) --live $(INPUTS)

demo: ## The flagship demonstration, end to end, against the real sandbox
	./scripts/with-sandbox.sh ./scripts/demo.sh

quickstart: ## The path a new contributor takes -- timed in CI
	@$(MAKE) --no-print-directory install
	@$(MAKE) --no-print-directory verify
	@$(MAKE) --no-print-directory test

clean: ## Remove caches and local artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache .verity build dist
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
