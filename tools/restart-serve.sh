#!/usr/bin/env bash
# Restarting the dashboard: stop the running `cburn serve` and bring it up again
# from the current code. Needed after backend edits - a running process holds the
# old code and, for instance, knows nothing about new endpoints.
#
# Run:  tools/restart-serve.sh
# Logs: ~/.local/share/cburn/serve.log

set -euo pipefail

cd "$(dirname "$0")/.."

CBURN=".venv/bin/cburn"
LOG="$HOME/.local/share/cburn/serve.log"

[ -x "$CBURN" ] || { echo "no $CBURN - build the environment: pip install -e '.[dev]'" >&2; exit 1; }

# The port comes from the config rather than being hardcoded: it is edited in config.toml.
PORT="$("$CBURN" paths | awk '/^port/ {print $NF}')"
PORT="${PORT:-8799}"

AGENT="gui/$(id -u)/com.cloudo.cburn"

if /bin/launchctl print "$AGENT" >/dev/null 2>&1; then
  # With the autostart agent launchd owns the process: a SIGTERM of our own would only make
  # it raise a new copy, and that copy would then take the lock away from ours. `kickstart -k`
  # is launchd's own restart.
  echo "the launchd agent holds the dashboard, restarting through it"
  /bin/launchctl kickstart -k "$AGENT"
  NEW=""
else
  # SIGTERM, not KILL: the server must have time to close the watcher and the connections.
  PIDS="$(pgrep -f "cburn serve" || true)"
  if [ -n "$PIDS" ]; then
    echo "stopping: $(echo "$PIDS" | tr '\n' ' ')"
    # shellcheck disable=SC2086
    kill $PIDS 2>/dev/null || true
    for _ in $(seq 30); do
      pgrep -f "cburn serve" >/dev/null || break
      sleep 0.2
    done
    if pgrep -f "cburn serve" >/dev/null; then
      echo "did not stop within 6 s, killing" >&2
      pkill -9 -f "cburn serve" || true
    fi
  fi

  mkdir -p "$(dirname "$LOG")"
  nohup "$CBURN" serve --port "$PORT" >>"$LOG" 2>&1 &
  NEW=$!
  disown "$NEW" 2>/dev/null || true
fi

# "Restarted" means "answers", not "a process was created".
for _ in $(seq 50); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "dashboard is up: http://localhost:$PORT${NEW:+ (pid $NEW)}, logs $LOG"
    exit 0
  fi
  if [ -n "$NEW" ] && ! kill -0 "$NEW" 2>/dev/null; then
    echo "the process died, see $LOG" >&2
    tail -5 "$LOG" >&2
    exit 1
  fi
  sleep 0.2
done

echo "port $PORT did not answer within 10 s, see $LOG" >&2
tail -5 "$LOG" >&2
exit 1
