# The project's commands in one place; `make` on its own lists them.
#
# The Python side lives in .venv (pip, there is no uv on this machine), the frontend in web/,
# and the desktop wrapper is built from the root - Tauri looks for src-tauri/ next to itself.

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
CBURN := $(VENV)/bin/cburn
RUFF := $(VENV)/bin/ruff
MYPY := $(VENV)/bin/mypy
CARGO := $(HOME)/.cargo/bin

.DEFAULT_GOAL := help
.PHONY: help install venv check test test-real lint format types web dev serve restart \
        reindex paths stats otel desktop desktop-build clean

help: ## show this list
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: venv ## set up .venv and the npm dependencies
	$(PIP) install -e ".[dev]"
	npm install
	cd web && npm install

venv:
	test -d $(VENV) || python3 -m venv $(VENV)

check: test lint types ## the three checks that must pass before a commit

test: ## the test suite
	$(PY) -m pytest -q

test-real: ## the smoke test over the real ~/.claude history (machine-dependent, slow)
	$(PY) -m pytest -m real_history -q -s

lint: ## ruff: the linter and the format check
	$(RUFF) check .
	$(RUFF) format --check .

format: ## ruff: fix what can be fixed and reformat
	$(RUFF) check --fix .
	$(RUFF) format .

types: ## mypy
	$(MYPY)

web: ## build the frontend into web/dist
	cd web && npm install && npm run build

dev: ## the frontend with hot reload; the API is proxied to a running serve
	cd web && npm run dev

serve: ## the dashboard on http://127.0.0.1:8799
	$(CBURN) serve

restart: ## restart the running dashboard from the current code
	tools/restart-serve.sh

reindex: ## read the transcripts into the database
	$(CBURN) reindex

paths: ## where the config, the database and the transcripts live
	$(CBURN) paths

stats: ## the spend summary for the last week
	$(CBURN) stats

otel: ## what the telemetry receiver got
	$(CBURN) otel

desktop: ## the desktop window with hot reload: vite for the page, cargo for the Rust
	PATH="$(CARGO):$$PATH" npm run desktop

desktop-build: ## build the .app into src-tauri/target/release/bundle/macos
	PATH="$(CARGO):$$PATH" npm run desktop:build

clean: ## remove the build output and the tool caches
	rm -rf web/dist .pytest_cache .mypy_cache .ruff_cache
	find src tests -name __pycache__ -type d -prune -exec rm -rf {} +
