"""cburn - local dashboard for Claude Code token spend."""

from importlib.metadata import PackageNotFoundError, version

try:
    #: The version is declared in `pyproject.toml` alone: a second copy in the code drifts,
    #: and `cburn --version` was answering 0.1.0 out of a 0.1.1 release because of it.
    __version__ = version("cburn")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0"
