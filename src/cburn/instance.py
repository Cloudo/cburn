"""One `cburn serve` per machine: the lock that keeps a second one from starting.

Two servers are not a duplicate of the dashboard but a duplicate of everything behind it:
two watchers reading the same transcripts, two advisor loops asking `claude -p` for money,
two notification tickers sending the same alert to the phone twice. SQLite has a single
writer, so they would fight over it as well.

The port does not protect against this - a second server started with `--port 8800` binds
happily and does all the same work. What protects is a lock on the state directory:
`flock` holds while the process is alive and is released by the kernel on any death, so a
crash does not leave a stale lock behind and no "is the pid alive" guessing is needed.
"""

from __future__ import annotations

import errno
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .paths import DATA_DIR, ensure_dirs

#: The lock file. Empty of meaning by itself - what matters is the lock on the descriptor.
LOCK_PATH = DATA_DIR / "serve.lock"


class AlreadyRunning(RuntimeError):
    """A server is already holding the lock. `pid` is its process, if it managed to write one."""

    def __init__(self, pid: int | None) -> None:
        self.pid = pid
        whose = f" (pid {pid})" if pid else ""
        super().__init__(f"cburn serve is already running{whose}")


@contextmanager
def only_one(path: Path | None = None) -> Iterator[None]:
    """Hold the lock for the duration of the block, or raise `AlreadyRunning`."""
    if path is None:
        # `ensure_dirs` and not a bare mkdir: creating the state directory ahead of the
        # migration would leave the data of the former name where it lies.
        ensure_dirs()
        path = LOCK_PATH
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
            raise
        handle.seek(0)
        owner = handle.read().strip()
        handle.close()
        raise AlreadyRunning(int(owner) if owner.isdigit() else None) from None
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    try:
        yield
    finally:
        # The file itself stays in place: unlinking it would take away a lock that
        # the next server may already be holding.
        fcntl.flock(handle, fcntl.LOCK_UN)
        handle.close()
