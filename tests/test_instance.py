"""The single-copy lock of the server (`cburn serve`).

The check is done in a real second process: `flock` is per descriptor, so within one
process the lock is taken twice without a hitch and the test would prove nothing.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from cburn import instance

HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from cburn import instance
    with instance.only_one(Path(sys.argv[1])):
        print("held", flush=True)
        time.sleep(30)
    """
)


@pytest.fixture
def holder(tmp_path: Path):
    """A neighbouring process holding the lock."""
    lock = tmp_path / "serve.lock"
    process = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(lock)],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "held"
    try:
        yield lock, process
    finally:
        process.kill()
        process.wait()


def test_second_start_is_refused(holder) -> None:
    lock, process = holder
    with pytest.raises(instance.AlreadyRunning) as caught:
        with instance.only_one(lock):
            pass
    # The pid is named so that a human can see who exactly is holding the port.
    assert caught.value.pid == process.pid


def test_lock_is_released_when_the_holder_dies(holder) -> None:
    """A crash leaves no stale lock: the kernel releases it together with the process."""
    lock, process = holder
    process.kill()
    process.wait()
    with instance.only_one(lock):
        pass  # taken without a hitch


def test_lock_is_free_after_a_normal_exit(tmp_path: Path) -> None:
    lock = tmp_path / "serve.lock"
    with instance.only_one(lock):
        pass
    with instance.only_one(lock):
        pass
