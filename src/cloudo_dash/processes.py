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
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: Запуск бинаря небыстрый (около 1,3 с), поэтому список кэшируется.
TIMEOUT = 30.0

#: Столько живёт кэш списка сессий: сессии не появляются чаще.
CACHE_SECONDS = 15.0

CLAUDE_BINARY = "claude"

#: (момент запроса, ответ). None в ответе — спросить не удалось.
_cache: tuple[float, list[ClaudeSession] | None] = (0.0, None)


@dataclass(frozen=True)
class ClaudeSession:
    """Активная сессия Claude Code, как её видит сам Claude Code."""

    pid: int
    session_id: str
    cwd: str | None = None
    kind: str | None = None
    name: str | None = None


def active_sessions(*, use_cache: bool = False) -> list[ClaudeSession]:
    """Спросить у Claude Code список его сессий.

    Пустой список означает и «сессий нет», и «спросить не удалось». Там, где
    разница важна, берите `active_session_ids`.
    """
    return _ask(use_cache=use_cache) or []


def active_session_ids(*, use_cache: bool = True) -> set[str] | None:
    """Идентификаторы живых сессий; None — спросить не удалось.

    Отличать одно от другого обязательно: молчащий `claude` не повод объявить
    все сессии завершёнными.
    """
    sessions = _ask(use_cache=use_cache)
    return None if sessions is None else {session.session_id for session in sessions}


def _ask(*, use_cache: bool) -> list[ClaudeSession] | None:
    global _cache
    asked_at, cached = _cache
    if use_cache and cached is not None and time.monotonic() - asked_at < CACHE_SECONDS:
        return cached
    sessions = _run()
    if sessions is not None:
        _cache = (time.monotonic(), sessions)
    return sessions


def _run() -> list[ClaudeSession] | None:
    try:
        result = subprocess.run(
            [CLAUDE_BINARY, "agents", "--json"],
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("не удалось получить список сессий Claude Code: %s", exc)
        return None
    try:
        rows = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        log.warning("список сессий Claude Code не разобран: %s", exc)
        return None
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
    """Процесс сессии по её идентификатору. Кэш не используется: закрываем по нему."""
    return next((s for s in active_sessions() if s.session_id == session_id), None)


def terminate(pid: int) -> bool:
    """Попросить процесс завершиться (SIGTERM). Насильно не убиваем."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        log.warning("не удалось завершить процесс %s: %s", pid, exc)
        return False
    return True
