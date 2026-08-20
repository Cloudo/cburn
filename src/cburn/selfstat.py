"""What cburn costs itself: the CPU and the memory of its own processes.

A speedometer that eats a core while showing the burn rate is a joke about itself, so
the figure has to be visible while the thing is being written. It is shown in the
development build alone - the dashboard of a person who merely uses cburn has no
business carrying it.

Two processes are measured, and by two different means, on purpose:

* the server measures itself. `resource.getrusage` gives the CPU seconds spent, and the
  share is the difference between two polls over the real time between them - a burst
  shows up at once, which is the whole point while developing;
* the desktop application is somebody else's process, and only `ps` can be asked about
  it. Its `%cpu` is an average decaying over about a minute, so it lags behind a burst
  instead of catching it.

Neither number needs a dependency: `resource` is in the standard library and `ps` is in
every macOS.
"""

from __future__ import annotations

import os
import resource
import subprocess
import time
from dataclasses import dataclass
from typing import Any

#: How the bundled application and the development binary look in a process list. The
#: python server is not matched by it - it lives under `.venv/bin/cburn`.
APP_PATTERN = r"cburn\.app/Contents/MacOS/cburn|src-tauri/target/[a-z]*/cburn"

#: `ps` is asked once per poll and must not hold the answer up.
PS_TIMEOUT = 2.0


def _cpu_seconds() -> float:
    """The processor time of the server and of anything it spawned."""
    me = resource.getrusage(resource.RUSAGE_SELF)
    kids = resource.getrusage(resource.RUSAGE_CHILDREN)
    return me.ru_utime + me.ru_stime + kids.ru_utime + kids.ru_stime


def _ps(pids: list[int]) -> dict[int, dict[str, float]]:
    """`%cpu` and the resident size of the given processes, as `ps` sees them."""
    if not pids:
        return {}
    try:
        out = subprocess.run(
            ["ps", "-o", "pid=,%cpu=,rss=", "-p", ",".join(str(pid) for pid in pids)],
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    found: dict[int, dict[str, float]] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        try:
            found[int(parts[0])] = {"cpu_percent": float(parts[1]), "rss_mb": int(parts[2]) / 1024}
        except ValueError:
            continue
    return found


def _app_pid() -> int | None:
    """The desktop application, if it is running. There is only ever one of it."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", APP_PATTERN], capture_output=True, text=True, timeout=PS_TIMEOUT
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.split()
    return int(first[0]) if first else None


@dataclass
class _Sample:
    at: float
    cpu_seconds: float


class SelfStat:
    """The cost of cburn itself, kept between polls so the share has an interval to be of."""

    def __init__(self) -> None:
        self._previous: _Sample | None = None

    def read(self) -> dict[str, Any]:
        now = time.monotonic()
        cpu = _cpu_seconds()
        previous, self._previous = self._previous, _Sample(now, cpu)

        window = now - previous.at if previous else 0.0
        # The very first poll has nothing to compare against: honest silence beats a
        # number invented out of the process lifetime.
        share = (cpu - previous.cpu_seconds) / window * 100 if previous and window > 0 else None

        pid = os.getpid()
        app_pid = _app_pid()
        measured = _ps([pid, app_pid] if app_pid else [pid])

        server = measured.get(pid, {})
        app = measured.get(app_pid, {}) if app_pid else {}
        return {
            "server": {
                "pid": pid,
                # Ours is the exact share over the window; `ps` is only asked for memory.
                "cpu_percent": round(share, 1) if share is not None else None,
                "rss_mb": round(server.get("rss_mb", 0.0), 1),
            },
            "app": (
                {
                    "pid": app_pid,
                    "cpu_percent": round(app.get("cpu_percent", 0.0), 1),
                    "rss_mb": round(app.get("rss_mb", 0.0), 1),
                }
                if app_pid
                else None
            ),
            "window_seconds": round(window, 2),
        }
