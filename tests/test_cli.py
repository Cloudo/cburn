"""Тесты CLI и сверка цифр по сессии (задача A3, критерий приёмки M1).

Главный тест — `test_session_output_matches_independent_count`: суммы, которые
печатает `cdash session`, сверяются с независимым подсчётом по сырому JSON,
сделанным так же, как это делается вручную через `jq`. Расхождение должно быть
нулевым.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

import pytest

from cloudo_dash import cli, paths

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "transcripts"
FIXTURES = sorted(FIXTURES_DIR.glob("*.jsonl"))


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Изолированные каталог транскриптов и БД вместо настоящих."""
    projects_dir = tmp_path / "projects"
    (projects_dir / "проект").mkdir(parents=True)
    monkeypatch.setattr(paths, "CLAUDE_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "CONFIG_PATH", tmp_path / "config.toml")
    return projects_dir / "проект"


def jq_style_totals(path: Path) -> dict[str, dict[str, int]]:
    """Независимый подсчёт по сырому JSON: группировка по message.id, максимум usage.

    Ровно то же, что делает ручная сверка:
    `jq -s 'group_by(.message.id) | map(max)'`.
    """
    per_session: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
    for line in path.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") != "assistant":
            continue
        message = record.get("message") or {}
        if message.get("model") == "<synthetic>":
            continue
        usage = message.get("usage") or {}
        creation = usage.get("cache_creation") or {}
        current = {
            "вход": usage.get("input_tokens", 0),
            "выход": usage.get("output_tokens", 0),
            "кэш чтение": usage.get("cache_read_input_tokens", 0),
            "кэш 5m": creation.get("ephemeral_5m_input_tokens", 0),
            "кэш 1h": creation.get("ephemeral_1h_input_tokens", 0),
        }
        known = per_session[record["sessionId"]].get(message["id"])
        if known is None:
            per_session[record["sessionId"]][message["id"]] = current
        else:
            for key, value in current.items():
                known[key] = max(known[key], value)

    return {
        session_id: {
            "ходов": len(turns),
            **{
                key: sum(turn[key] for turn in turns.values())
                for key in ("вход", "выход", "кэш чтение", "кэш 5m", "кэш 1h")
            },
        }
        for session_id, turns in per_session.items()
    }


def parse_output(text: str) -> dict[str, int]:
    """Вытащить числа из вывода `cdash session`."""
    numbers: dict[str, int] = {}
    for label, pattern in (
        ("ходов", r"^ходов\s+: (\d+)"),
        ("вход", r"^вход\s+: ([\d ]+)"),
        ("выход", r"^выход\s+: ([\d ]+)"),
        ("кэш чтение", r"^кэш чтение\s+: ([\d ]+)"),
        ("кэш 5m", r"5m ([\d ]+),"),
        ("кэш 1h", r"1h ([\d ]+)\)"),
    ):
        match = re.search(pattern, text, re.MULTILINE)
        assert match, f"в выводе нет строки «{label}»:\n{text}"
        numbers[label] = int(match.group(1).replace(" ", ""))
    return numbers


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.stem)
def test_session_output_matches_independent_count(
    project: Path, path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Расхождение между `cdash session` и ручным подсчётом — ноль."""
    target = project / path.name
    target.write_text(path.read_text())
    assert cli.main(["reindex"]) == 0
    capsys.readouterr()

    expected = jq_style_totals(target)
    assert expected, "в фикстуре нет ходов ассистента"
    for session_id, totals in expected.items():
        assert cli.main(["session", session_id]) == 0
        assert parse_output(capsys.readouterr().out) == totals


def test_session_accepts_id_prefix(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = project / FIXTURES[0].name
    target.write_text(FIXTURES[0].read_text())
    cli.main(["reindex"])
    capsys.readouterr()

    session_id = next(iter(jq_style_totals(target)))
    assert cli.main(["session", session_id[:8]]) == 0
    assert session_id in capsys.readouterr().out


def test_session_reports_unknown_id(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cli.main(["reindex"])
    capsys.readouterr()
    assert cli.main(["session", "нет-такой"]) == 1
    assert "не найдена" in capsys.readouterr().err


def test_reindex_is_idempotent(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / FIXTURES[0].name).write_text(FIXTURES[0].read_text())
    assert cli.main(["reindex"]) == 0
    first = capsys.readouterr().out
    assert cli.main(["reindex"]) == 0
    second = capsys.readouterr().out

    assert "прочитано строк: 0" not in first
    assert "прочитано строк: 0" in second
    assert "новых ходов: 0," in second


def test_sessions_lists_indexed(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (project / FIXTURES[0].name).write_text(FIXTURES[0].read_text())
    cli.main(["reindex"])
    capsys.readouterr()

    assert cli.main(["sessions", "-n", "5"]) == 0
    out = capsys.readouterr().out
    assert out.strip(), "список сессий пуст"
    assert "проект" in out


def test_sessions_on_empty_db(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sessions"]) == 1
    assert "cdash reindex" in capsys.readouterr().err


def test_paths_and_initdb(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["paths"]) == 0
    assert "read-only" in capsys.readouterr().out
    assert cli.main(["initdb"]) == 0
    assert "turns" in capsys.readouterr().out


def test_unimplemented_command_reports_milestone(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["serve"]) == 2
    assert "M2" in capsys.readouterr().err
