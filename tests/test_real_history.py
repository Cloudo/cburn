"""A smoke test over the real `~/.claude/projects` history (task B8).

It does not run by default: the test depends on the machine rather than on the
repository, and CI has no data for it. To run it by hand:

    .venv/bin/python -m pytest -m real_history -q -s

What it checks. The transcript format is undocumented and changes between Claude Code
versions, so the only real requirement for the parser is tolerance: an unknown record
must not bring the walk down. The test fails only on a parsing exception; everything
else it prints as a report, so that after a Claude Code update it is visible what
changed.

Anonymised fixtures of versions 2.1.220-228 live in `fixtures/transcripts/`
and run in the ordinary pass - they cover parsing without the real history.
"""

from __future__ import annotations

import collections
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cburn import config, paths, pricing
from cburn.analyzer import digest
from cburn.collector.indexer import ingest_tree
from cburn.collector.parser import RecordKind, parse_line
from cburn.db import connect

pytestmark = pytest.mark.real_history


@pytest.fixture
def history() -> Path:
    root = paths.CLAUDE_PROJECTS_DIR
    if not root.is_dir() or not any(root.rglob("*.jsonl")):
        pytest.skip(f"no transcripts in {root}")
    return root


def test_parser_survives_every_line(history: Path) -> None:
    """Not a single line of the real history brings parsing down."""
    kinds: collections.Counter[str] = collections.Counter()
    unknown: collections.Counter[tuple[str, str]] = collections.Counter()
    broken: list[str] = []
    files = lines = 0

    for path in sorted(history.rglob("*.jsonl")):
        files += 1
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line_no, raw in enumerate(handle, start=1):
                lines += 1
                try:
                    record = parse_line(raw)
                except Exception as exc:  # noqa: BLE001 - exactly what the test catches
                    broken.append(f"{path}:{line_no}: {exc!r}")
                    continue
                if record is None:
                    kinds["not parsed"] += 1
                    continue
                kinds[record.kind] += 1
                if record.kind is RecordKind.UNKNOWN:
                    unknown[(record.raw_type, record.version or "-")] += 1

    print(f"\nfiles {files}, lines {lines}")
    for kind, count in kinds.most_common():
        print(f"  {kind:<14} {count}")
    print("unknown types (type, version):")
    for (raw_type, version), count in unknown.most_common(10):
        print(f"  {raw_type:<24} {version:<10} {count}")

    assert not broken, "parsing failed on lines:\n" + "\n".join(broken[:20])
    assert kinds[RecordKind.ASSISTANT] > 0, "no turns found - the parser lost the format"


def test_full_ingest_survives_history(history: Path, tmp_path: Path) -> None:
    """A full walk over the history into a clean database: no failures and a non-empty result."""
    started = time.monotonic()
    conn = connect(tmp_path / "real.db")
    results = ingest_tree(conn, history)
    elapsed = time.monotonic() - started

    row = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM turns)                        AS turns,
               (SELECT COUNT(*) FROM sessions)                     AS sessions,
               (SELECT COUNT(*) FROM projects)                     AS projects,
               (SELECT COUNT(*) FROM sessions
                 WHERE parent_session_id IS NOT NULL)              AS linked,
               (SELECT COALESCE(SUM(seen), 0) FROM raw_event_counts) AS unknown
        """
    ).fetchone()
    print(
        f"\nfiles {len(results)}, turns {row['turns']}, sessions {row['sessions']},"
        f" projects {row['projects']}, resume links {row['linked']},"
        f" unknown records {row['unknown']}, in {elapsed:.1f} s"
    )

    assert row["turns"] > 0
    assert row["sessions"] > 0
    # A turn cannot belong to a non-existent session: referential integrity
    # breaks more quietly than an exception, and there is nothing else to catch it.
    orphans = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE session_id NOT IN (SELECT id FROM sessions)"
    ).fetchone()[0]
    assert orphans == 0


def test_digest_finds_the_signals_the_report_found(history: Path, tmp_path: Path) -> None:
    """The M3 acceptance (task D7): the digest sees the same things that were found by hand.

    The prototype report on navuik/core rested on three things: an overgrown mega-session,
    idle turns and one and the same script driven through a heredoc dozens of times. The
    model call itself is not part of this - it costs money and is done by hand; what is
    checked is that the advisor has something to lean on.
    """
    conn = connect(tmp_path / "acceptance.db")
    ingest_tree(conn, history)
    # Prices come from the user config: without them the cost is zero, while
    # "heavy sessions" is a question about money.
    pricing.recalculate(conn, config.load())
    payload = digest.build(conn, datetime.now(UTC) - timedelta(days=30), config=config.load())

    heavy = max(payload["sessions"], key=lambda row: row["cost_usd"])
    print(
        f"\nmega-session {heavy['id'][:8]}: turns {heavy['turns']}, "
        f"${heavy['cost_usd']:.2f}, over the threshold {heavy['over_context_limit']}"
    )
    print(f"idle turns: {payload['idle']['turns']} ({payload['idle']['share']:.1%})")
    print(f"heredoc: {payload['tools']['heredoc_calls']} calls")

    assert heavy["turns"] > 500, "the mega-session must be visible"
    assert heavy["over_context_limit"], "and must be marked as past the threshold"
    assert payload["idle"]["turns"] > 0, "idle turns must be counted"
    assert payload["tools"]["heredoc_calls"] > 0, "repeated heredocs must be visible"
    assert payload["size"]["within_limit"], "the digest must fit into the limit"
