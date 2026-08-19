#!/usr/bin/env bash
# The Tauri app on the demo dataset. The app follows CBURN_PORT, so the demo keeps its
# own port and the real dashboard stays untouched on :8799.
#
# Run:  tools/demo-app.sh   (or `make demo-app`)
# Logs: ~/.local/share/cburn-demo/serve.log

set -euo pipefail

cd "$(dirname "$0")/.."

PY=".venv/bin/python"
CBURN=".venv/bin/cburn"
ROOT="${CBURN_DEMO_ROOT:-$HOME/.local/share/cburn-demo}"
APP="src-tauri/target/release/bundle/macos/cburn.app"
PORT=8798

[ -x "$CBURN" ] || { echo "no $CBURN - build the environment: pip install -e '.[dev]'" >&2; exit 1; }
[ -d "$APP" ] || { echo "no $APP - build it first: make desktop-build" >&2; exit 1; }

# A fresh dataset every run: the times are anchored to "now", and yesterday's run looks
# stale today. The demo server holds the old database open - stop it before the wipe;
# it is found by its listening port, never by a process-name pattern that could catch
# the real server too.
DEMO_PID="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN || true)"
if [ -n "$DEMO_PID" ]; then
  # shellcheck disable=SC2086
  kill $DEMO_PID 2>/dev/null || true
  sleep 1
fi

"$PY" tools/demo_data.py --root "$ROOT"

# The demo environment is exported for both the server and the app below: if the app
# ever raises a server itself, it must raise the demo one, not the real one.
export CLAUDE_CONFIG_DIR="$ROOT/claude" CBURN_CONFIG="$ROOT/config.toml" CBURN_DATA_DIR="$ROOT/data"
nohup "$CBURN" serve >>"$ROOT/serve.log" 2>&1 &
disown

for _ in $(seq 50); do
  curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 && break
  sleep 0.2
done
curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1 \
  || { echo "the demo server did not answer, see $ROOT/serve.log" >&2; exit 1; }

"$PY" tools/demo_data.py --root "$ROOT" --tick

# The application runs in a single copy: a real one already in the tray would swallow
# this launch and keep showing the real port, so it is asked to quit first.
if pgrep -f "cburn.app/Contents/MacOS/cburn" >/dev/null; then
  osascript -e 'tell application "cburn" to quit' >/dev/null 2>&1 || true
  sleep 1
fi
CBURN_PORT=$PORT nohup "$APP/Contents/MacOS/cburn" >/dev/null 2>&1 &
disown

echo "the tauri app is on the demo: http://127.0.0.1:$PORT (the real dashboard keeps :8799)"
echo "liven the needle before a shot:  make demo-tick"
echo "back to the real app: quit it in the tray, then open it the usual way"
