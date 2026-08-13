"""Автозапуск дашборда через launchd (задача C5, ТЗ §10).

Агент пользователя, а не демон системы: дашборд читает `~/.claude` и пишет в
`~/.local/share`, root ему не нужен и вреден. Ставится в
`~/Library/LaunchAgents/com.cloudo.cloudo-dash.plist`, логи ложатся рядом с
базой — туда же, куда пишет `tools/restart-serve.sh`.

Запускается не консольный скрипт `cdash`, а `python -m cloudo_dash`: путь до
интерпретатора известен точно, а `cdash` может оказаться не тем — например,
из другого окружения в PATH.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import paths

LABEL = "com.cloudo.cloudo-dash"

#: Запуск внешней команды вынесен в тип, чтобы тесты не трогали launchd машины.
Runner = Callable[..., tuple[int, str]]

#: launchctl из macOS 11+. На более старых системах вместо bootstrap/bootout
#: были load/unload — если понадобится, добавим запасной путь по факту.
LAUNCHCTL = "/bin/launchctl"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return paths.DATA_DIR / "serve.log"


def build_plist(port: int, executable: Path | None = None) -> bytes:
    """Собрать plist агента.

    `KeepAlive` только на неудачный выход: иначе launchd будет поднимать
    дашборд после каждой остановки руками, в том числе при `cdash uninstall`.
    """
    log = str(log_path())
    document = {
        "Label": LABEL,
        "ProgramArguments": [
            str(executable or Path(sys.executable)),
            "-m",
            "cloudo_dash",
            "serve",
            "--port",
            str(port),
        ],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ProcessType": "Background",
        "StandardOutPath": log,
        "StandardErrorPath": log,
        "WorkingDirectory": str(Path.home()),
    }
    return plistlib.dumps(document)


def install(port: int, run: Runner | None = None) -> str:
    """Поставить агент и запустить его. Повторный вызов обновляет plist."""
    if sys.platform != "darwin":
        raise SystemExit("автозапуск через launchd есть только на macOS")
    runner = run or _run
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.ensure_dirs()
    target.write_bytes(build_plist(port))

    domain = f"gui/{_uid()}"
    # Прежний агент выгружается молча: его может не быть, и это не ошибка.
    runner([LAUNCHCTL, "bootout", f"{domain}/{LABEL}"], check=False)
    code, err = runner([LAUNCHCTL, "bootstrap", domain, str(target)], check=False)
    if code != 0:
        raise SystemExit(f"launchctl не смог загрузить агент: {err.strip() or code}")
    return f"агент {LABEL} поставлен: порт {port}, логи {log_path()}"


def uninstall(run: Runner | None = None) -> str:
    """Снять агент и убрать plist. Уже снятый агент не считается ошибкой."""
    if sys.platform != "darwin":
        raise SystemExit("автозапуск через launchd есть только на macOS")
    runner = run or _run
    target = plist_path()
    runner([LAUNCHCTL, "bootout", f"gui/{_uid()}/{LABEL}"], check=False)
    if target.exists():
        target.unlink()
        return f"агент {LABEL} снят, {target} удалён"
    return f"агента {LABEL} и не было"


def status(run: Runner | None = None) -> str:
    """Что launchd думает про агент прямо сейчас."""
    if not plist_path().exists():
        return "агент не поставлен — `cdash install`"
    runner = run or _run
    code, out = runner([LAUNCHCTL, "print", f"gui/{_uid()}/{LABEL}"], check=False)
    if code != 0:
        return f"plist на месте, но launchd агента не знает — попробуйте `cdash install`\n{out}"
    state = next((line.strip() for line in out.splitlines() if "state =" in line), "загружен")
    pid = next((line.strip() for line in out.splitlines() if line.strip().startswith("pid =")), "")
    return f"агент {LABEL}: {state} {pid}".strip()


def _run(command: list[str], check: bool = True) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(command)}: {result.stderr.strip()}")
    return result.returncode, result.stdout + result.stderr


def _uid() -> int:
    return os.getuid()
