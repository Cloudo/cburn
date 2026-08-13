"""Смоук на настоящей истории `~/.claude/projects` (задача B8).

По умолчанию не гоняется: тест зависит от машины, а не от репозитория, и в CI
его данных нет. Запуск руками:

    .venv/bin/python -m pytest -m real_history -q -s

Что он проверяет. Формат транскриптов недокументирован и меняется между
версиями Claude Code, поэтому единственное настоящее требование к парсеру —
терпимость: незнакомая запись не должна ронять обход. Тест падает только на
исключении разбора; всё остальное он печатает как отчёт, чтобы после
обновления Claude Code было видно, что изменилось.

Обезличенные фикстуры версий 2.1.220–228 лежат в `fixtures/transcripts/`
и гоняются обычным прогоном — они страхуют разбор без реальной истории.
"""

from __future__ import annotations

import collections
import time
from pathlib import Path

import pytest

from cloudo_dash import paths
from cloudo_dash.collector.indexer import ingest_tree
from cloudo_dash.collector.parser import RecordKind, parse_line
from cloudo_dash.db import connect

pytestmark = pytest.mark.real_history


@pytest.fixture
def history() -> Path:
    root = paths.CLAUDE_PROJECTS_DIR
    if not root.is_dir() or not any(root.rglob("*.jsonl")):
        pytest.skip(f"нет транскриптов в {root}")
    return root


def test_parser_survives_every_line(history: Path) -> None:
    """Ни одна строка настоящей истории не роняет разбор."""
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
                except Exception as exc:  # noqa: BLE001 — ровно то, что ловит тест
                    broken.append(f"{path}:{line_no}: {exc!r}")
                    continue
                if record is None:
                    kinds["не разобрана"] += 1
                    continue
                kinds[record.kind] += 1
                if record.kind is RecordKind.UNKNOWN:
                    unknown[(record.raw_type, record.version or "—")] += 1

    print(f"\nфайлов {files}, строк {lines}")
    for kind, count in kinds.most_common():
        print(f"  {kind:<14} {count}")
    print("незнакомые типы (тип, версия):")
    for (raw_type, version), count in unknown.most_common(10):
        print(f"  {raw_type:<24} {version:<10} {count}")

    assert not broken, "разбор упал на строках:\n" + "\n".join(broken[:20])
    assert kinds[RecordKind.ASSISTANT] > 0, "ходов не нашлось — парсер потерял формат"


def test_full_ingest_survives_history(history: Path, tmp_path: Path) -> None:
    """Полный обход истории в чистую БД: без падений и с непустым результатом."""
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
        f"\nфайлов {len(results)}, ходов {row['turns']}, сессий {row['sessions']},"
        f" проектов {row['projects']}, связей resume {row['linked']},"
        f" незнакомых записей {row['unknown']}, за {elapsed:.1f} с"
    )

    assert row["turns"] > 0
    assert row["sessions"] > 0
    # Ход не может принадлежать несуществующей сессии: ссылочная целостность
    # ломается тише, чем исключение, и заметить её иначе нечем.
    orphans = conn.execute(
        "SELECT COUNT(*) FROM turns WHERE session_id NOT IN (SELECT id FROM sessions)"
    ).fetchone()[0]
    assert orphans == 0
