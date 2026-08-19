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
        reindex paths stats otel desktop desktop-build clean demo demo-tick demo-app

DEMO_ROOT := $(HOME)/.local/share/cburn-demo
DEMO_ENV := CLAUDE_CONFIG_DIR=$(DEMO_ROOT)/claude CBURN_CONFIG=$(DEMO_ROOT)/config.toml \
	CBURN_DATA_DIR=$(DEMO_ROOT)/data

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

demo: ## generate the demo dataset and serve it on http://127.0.0.1:8798
	$(PY) tools/demo_data.py --root $(DEMO_ROOT)
	$(DEMO_ENV) $(CBURN) serve

demo-tick: ## refresh the demo's live sessions (right before a screenshot)
	$(PY) tools/demo_data.py --root $(DEMO_ROOT) --tick

demo-app: ## the tauri app on the demo dataset; the real dashboard keeps :8799
	tools/demo-app.sh

desktop: ## the desktop window with hot reload: vite for the page, cargo for the Rust
	@if pgrep -f 'cburn\.app/Contents/MacOS/cburn|src-tauri/target/[a-z]*/cburn' >/dev/null; then \
	  echo "cburn is already running. The application lives in a single copy, so this launch" >&2; \
	  echo "would be handed over to the one in the tray - and if that one came from" >&2; \
	  echo "\`make demo-app\`, you would be looking at the demo dataset and wondering why." >&2; \
	  echo "Quit it in the tray and run this again." >&2; \
	  exit 1; \
	fi
	@pids=$$(lsof -nP -tiTCP:5173 -sTCP:LISTEN); if [ -n "$$pids" ]; then \
	  echo "Port 5173 is taken (vite has strictPort, tauri waits for exactly this address):" >&2; \
	  ps -p $$pids -o pid,etime,command >&2; \
	  printf "Kill it and continue? [y/N] " >&2; \
	  read -r ans; \
	  case "$$ans" in \
	    [yY]) kill $$pids; sleep 1; \
	      if lsof -nP -tiTCP:5173 -sTCP:LISTEN >/dev/null; then \
	        echo "The port is still busy - deal with it by hand." >&2; exit 1; \
	      fi;; \
	    *) exit 1;; \
	  esac; \
	fi
	PATH="$(CARGO):$$PATH" npm run desktop

desktop-build: ## build the .app into src-tauri/target/release/bundle/macos
	PATH="$(CARGO):$$PATH" npm run desktop:build

clean: ## remove the build output and the tool caches
	rm -rf web/dist .pytest_cache .mypy_cache .ruff_cache
	find src tests -name __pycache__ -type d -prune -exec rm -rf {} +
