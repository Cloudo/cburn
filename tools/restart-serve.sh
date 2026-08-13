#!/usr/bin/env bash
# Перезапуск дашборда: остановить работающий `cdash serve` и поднять заново
# из текущего кода. Нужен после правок бэкенда — запущенный процесс держит
# старый код и, например, не знает новых эндпоинтов.
#
# Запуск: tools/restart-serve.sh
# Логи:   ~/.local/share/cloudo-dash/serve.log

set -euo pipefail

cd "$(dirname "$0")/.."

CDASH=".venv/bin/cdash"
LOG="$HOME/.local/share/cloudo-dash/serve.log"

[ -x "$CDASH" ] || { echo "нет $CDASH — соберите окружение: pip install -e '.[dev]'" >&2; exit 1; }

# Порт берётся из конфига, а не зашит: его правят в config.toml.
PORT="$("$CDASH" paths | awk '/^порт/ {print $NF}')"
PORT="${PORT:-8799}"

# SIGTERM, а не KILL: сервер должен успеть закрыть watcher и соединения.
PIDS="$(pgrep -f "cdash serve" || true)"
if [ -n "$PIDS" ]; then
  echo "останавливаю: $(echo "$PIDS" | tr '\n' ' ')"
  # shellcheck disable=SC2086
  kill $PIDS 2>/dev/null || true
  for _ in $(seq 30); do
    pgrep -f "cdash serve" >/dev/null || break
    sleep 0.2
  done
  if pgrep -f "cdash serve" >/dev/null; then
    echo "не остановился за 6 с, добиваю" >&2
    pkill -9 -f "cdash serve" || true
  fi
fi

mkdir -p "$(dirname "$LOG")"
nohup "$CDASH" serve --port "$PORT" >>"$LOG" 2>&1 &
NEW=$!
disown "$NEW" 2>/dev/null || true

# «Перезапустил» значит «отвечает», а не «процесс создан».
for _ in $(seq 50); do
  if curl -fsS --max-time 1 "http://127.0.0.1:$PORT/api/health" >/dev/null 2>&1; then
    echo "дашборд поднят: http://localhost:$PORT (pid $NEW, логи $LOG)"
    exit 0
  fi
  kill -0 "$NEW" 2>/dev/null || { echo "процесс упал, смотрите $LOG" >&2; tail -5 "$LOG" >&2; exit 1; }
  sleep 0.2
done

echo "порт $PORT не ответил за 10 с, смотрите $LOG" >&2
tail -5 "$LOG" >&2
exit 1
