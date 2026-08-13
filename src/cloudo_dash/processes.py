"""Поиск процессов Claude Code (кнопка «закрыть сессию»).

Точную связку `sessionId` → pid даёт сам Claude Code: `claude agents --json`
печатает активные сессии, включая интерактивные. В самом процессе `sessionId`
не найти — его нет ни в аргументах, ни в открытых дескрипторах: транскрипт
дописывается и сразу закрывается.

Про завершение. Своего обработчика сигналов у Claude Code нет: зарегистри-
рованные `SIGINT`/`SIGHUP`/`SIGTERM` вызывают немедленный `process.exit()`,
тогда как хуки `SessionEnd` выполняются асинхронно на штатном выходе (`/exit`,
Ctrl+D, `/clear`, logout). Поэтому SIGTERM закрывает сессию, но хуки
`SessionEnd` при этом, скорее всего, не отработают — об этом честно
предупреждает дашборд. Команды «закрыть чужую сессию» в CLI нет.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Запуск бинаря небыстрый, но вызывается он только при закрытии сессии.
TIMEOUT = 30.0

CLAUDE_BINARY = "claude"


@dataclass(frozen=True)
class ClaudeSession:
    """Активная сессия Claude Code, как её видит сам Claude Code."""

    pid: int
    session_id: str
    cwd: str | None = None
    kind: str | None = None
    name: str | None = None


def active_sessions() -> list[ClaudeSession]:
    """Спросить у Claude Code список его сессий."""
    try:
        result = subprocess.run(
            [CLAUDE_BINARY, "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("не удалось получить список сессий Claude Code: %s", exc)
        return []
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("список сессий Claude Code не разобран: %s", exc)
        return []
    sessions = []
    for row in rows if isinstance(rows, list) else []:
        pid, session_id = row.get("pid"), row.get("sessionId")
        if isinstance(pid, int) and isinstance(session_id, str):
            sessions.append(
                ClaudeSession(
                    pid=pid,
                    session_id=session_id,
                    cwd=row.get("cwd"),
                    kind=row.get("kind"),
                    name=row.get("name"),
                )
            )
    return sessions


def process_for_session(session_id: str) -> ClaudeSession | None:
    """Процесс сессии по её идентификатору."""
    return next((s for s in active_sessions() if s.session_id == session_id), None)


def terminate(pid: int) -> bool:
    """Попросить процесс завершиться (SIGTERM). Насильно не убиваем."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        log.warning("не удалось завершить процесс %s: %s", pid, exc)
        return False
    return True
