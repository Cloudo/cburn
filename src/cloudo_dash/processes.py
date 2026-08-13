"""Поиск процессов Claude Code (задача A8, «закрыть сессию»).

Сопоставить сессию с процессом точно нечем: `sessionId` не попадает ни в
аргументы командной строки, ни в открытые дескрипторы — транскрипт
дописывается и сразу закрывается. Единственная связка — рабочий каталог:
у процесса он берётся через `lsof`, у сессии это `cwd` из транскрипта.

Отсюда правило: завершать можно, только когда каталогу отвечает ровно один
процесс. Иначе дашборд честно говорит, что не знает, кого закрывать, — убить
чужую работающую сессию хуже, чем не закрыть ничего.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

#: По этой подстроке процесс опознаётся как Claude Code.
CLAUDE_BINARY_MARK = "native-binary/claude"

#: Сколько ждать внешние команды: они локальные и должны отвечать мгновенно.
TIMEOUT = 5.0


@dataclass(frozen=True)
class ClaudeProcess:
    pid: int
    cwd: str


def _run(args: list[str]) -> str:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("не удалось выполнить %s: %s", args[0], exc)
        return ""
    return result.stdout


def process_cwd(pid: int) -> str | None:
    """Рабочий каталог процесса."""
    for line in _run(["lsof", "-a", "-d", "cwd", "-p", str(pid), "-Fn"]).splitlines():
        if line.startswith("n"):
            return line[1:]
    return None


def claude_processes() -> list[ClaudeProcess]:
    """Все процессы Claude Code с их рабочими каталогами."""
    found: list[ClaudeProcess] = []
    for line in _run(["ps", "-eo", "pid=,command="]).splitlines():
        line = line.strip()
        if CLAUDE_BINARY_MARK not in line:
            continue
        pid_text, _, command = line.partition(" ")
        if "--claude-in-chrome-mcp" in command:  # вспомогательный процесс, не сессия
            continue
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        cwd = process_cwd(pid)
        if cwd:
            found.append(ClaudeProcess(pid=pid, cwd=cwd))
    return found


def process_for_cwd(cwd: str | None) -> ClaudeProcess | None:
    """Процесс сессии — только если каталогу отвечает ровно один процесс."""
    if not cwd:
        return None
    matches = [process for process in claude_processes() if process.cwd == cwd]
    return matches[0] if len(matches) == 1 else None


def terminate(pid: int) -> bool:
    """Попросить процесс завершиться (SIGTERM). Убивать насильно не будем."""
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError) as exc:
        log.warning("не удалось завершить процесс %s: %s", pid, exc)
        return False
    return True
