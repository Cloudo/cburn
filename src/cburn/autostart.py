"""Dashboard autostart via launchd (task C5, TZ §10).

A user agent, not a system daemon: the dashboard reads `~/.claude` and writes to
`~/.local/share`, so root is neither needed nor healthy. It is installed into
`~/Library/LaunchAgents/com.cloudo.cburn.plist`, and the logs land next to the
database - the same place `tools/restart-serve.sh` writes to.

What starts is not the `cburn` console script but `python -m cburn`: the interpreter
path is known exactly, while `cburn` may turn out to be the wrong one - say,
from another environment on PATH.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from . import paths

LABEL = "com.cloudo.cburn"

#: Running an external command is a type of its own so that tests never touch the machine's launchd.
Runner = Callable[..., tuple[int, str]]

#: launchctl from macOS 11+. On older systems load/unload stood in place of
#: bootstrap/bootout - if that is ever needed, a fallback path gets added on demand.
LAUNCHCTL = "/bin/launchctl"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def log_path() -> Path:
    return paths.DATA_DIR / "serve.log"


def build_plist(port: int, executable: Path | None = None) -> bytes:
    """Build the agent plist.

    `KeepAlive` only on a failed exit: otherwise launchd would raise the dashboard
    after every manual stop, including `cburn uninstall`.
    """
    log = str(log_path())
    document = {
        "Label": LABEL,
        "ProgramArguments": [
            str(executable or Path(sys.executable)),
            "-m",
            "cburn",
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
    """Install the agent and start it. A repeated call refreshes the plist."""
    if sys.platform != "darwin":
        raise SystemExit("autostart through launchd exists on macOS only")
    runner = run or _run
    target = plist_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    paths.ensure_dirs()
    target.write_bytes(build_plist(port))

    domain = f"gui/{_uid()}"
    # The previous agent is unloaded silently: it may not exist, and that is not an error.
    runner([LAUNCHCTL, "bootout", f"{domain}/{LABEL}"], check=False)
    code, err = runner([LAUNCHCTL, "bootstrap", domain, str(target)], check=False)
    if code != 0:
        raise SystemExit(f"launchctl could not load the agent: {err.strip() or code}")
    return f"agent {LABEL} installed: port {port}, logs {log_path()}"


def uninstall(run: Runner | None = None) -> str:
    """Remove the agent and delete the plist. An already removed agent is not an error."""
    if sys.platform != "darwin":
        raise SystemExit("autostart through launchd exists on macOS only")
    runner = run or _run
    target = plist_path()
    runner([LAUNCHCTL, "bootout", f"gui/{_uid()}/{LABEL}"], check=False)
    if target.exists():
        target.unlink()
        return f"agent {LABEL} removed, {target} deleted"
    return f"agent {LABEL} was not there"


def status(run: Runner | None = None) -> str:
    """What launchd thinks about the agent right now."""
    if not plist_path().exists():
        return "the agent is not installed - `cburn install`"
    runner = run or _run
    code, out = runner([LAUNCHCTL, "print", f"gui/{_uid()}/{LABEL}"], check=False)
    if code != 0:
        return f"the plist is there, but launchd does not know it - try `cburn install`\n{out}"
    state = next((line.strip() for line in out.splitlines() if "state =" in line), "loaded")
    pid = next((line.strip() for line in out.splitlines() if line.strip().startswith("pid =")), "")
    return f"agent {LABEL}: {state} {pid}".strip()


def _run(command: list[str], check: bool = True) -> tuple[int, str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise SystemExit(f"{' '.join(command)}: {result.stderr.strip()}")
    return result.returncode, result.stdout + result.stderr


def _uid() -> int:
    return os.getuid()
