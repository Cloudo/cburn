"""Tests of autostart through launchd (task C5).

The real `launchctl` is never called: it would touch the agents of the machine the
tests run on. A runner of our own is substituted instead, and what is checked is that
the commands and the plist come out the way they should.
"""

from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from cburn import autostart, paths


class FakeLaunchctl:
    """Remembers commands instead of running them and answers with a preset code."""

    def __init__(self, code: int = 0, output: str = "") -> None:
        self.calls: list[list[str]] = []
        self.code = code
        self.output = output

    def __call__(self, command: list[str], check: bool = True) -> tuple[int, str]:
        self.calls.append(command)
        return self.code, self.output


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The home directory and the data directory are temporary, not the real ones."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(paths, "CONFIG_PATH", tmp_path / "config.toml")
    monkeypatch.setattr(autostart.sys, "platform", "darwin")
    return tmp_path


def test_plist_runs_module_not_console_script(home: Path) -> None:
    """What starts is `python -m cburn`: the interpreter path is exact."""
    document = plistlib.loads(autostart.build_plist(8799, executable=Path("/opt/py/bin/python")))

    assert document["Label"] == "com.cloudo.cburn"
    assert document["ProgramArguments"] == [
        "/opt/py/bin/python",
        "-m",
        "cburn",
        "serve",
        "--port",
        "8799",
    ]
    assert document["RunAtLoad"] is True
    # Otherwise launchd would raise the dashboard after any manual stop.
    assert document["KeepAlive"] == {"SuccessfulExit": False}
    assert document["StandardOutPath"].endswith("serve.log")


def test_install_writes_plist_and_loads_agent(home: Path) -> None:
    launchctl = FakeLaunchctl()

    message = autostart.install(9000, run=launchctl)

    target = autostart.plist_path()
    assert target.exists()
    assert plistlib.loads(target.read_bytes())["ProgramArguments"][-1] == "9000"
    verbs = [call[1] for call in launchctl.calls]
    assert verbs == ["bootout", "bootstrap"], "the old agent is removed before the new one loads"
    assert "9000" in message


def test_install_twice_replaces_plist(home: Path) -> None:
    """A repeated install does not breed agents, it refreshes them."""
    autostart.install(8799, run=FakeLaunchctl())
    autostart.install(9100, run=FakeLaunchctl())

    document = plistlib.loads(autostart.plist_path().read_bytes())
    assert document["ProgramArguments"][-1] == "9100"


def test_install_reports_launchctl_failure(home: Path) -> None:
    """There is no silent install: a launchd refusal is visible to the human."""
    with pytest.raises(SystemExit, match="не смог загрузить"):
        autostart.install(8799, run=FakeLaunchctl(code=5, output="Load failed"))


def test_uninstall_removes_plist(home: Path) -> None:
    launchctl = FakeLaunchctl()
    autostart.install(8799, run=launchctl)

    message = autostart.uninstall(run=launchctl)

    assert not autostart.plist_path().exists()
    assert "снят" in message


def test_uninstall_without_agent_is_not_an_error(home: Path) -> None:
    assert "не было" in autostart.uninstall(run=FakeLaunchctl(code=3))


def test_status_without_plist(home: Path) -> None:
    assert "не поставлен" in autostart.status(run=FakeLaunchctl())


def test_status_reads_launchctl(home: Path) -> None:
    autostart.install(8799, run=FakeLaunchctl())

    text = autostart.status(run=FakeLaunchctl(output="\tstate = running\n\tpid = 4242\n"))

    assert "running" in text and "4242" in text
