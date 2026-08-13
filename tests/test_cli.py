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
from cloudo_dash.db import connect

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
    assert "project" in out  # имя из рабочего пути, а не slug каталога


def test_sessions_on_empty_db(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["sessions"]) == 1
    assert "cdash reindex" in capsys.readouterr().err


def test_paths_and_initdb(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["paths"]) == 0
    assert "read-only" in capsys.readouterr().out
    assert cli.main(["initdb"]) == 0
    assert "turns" in capsys.readouterr().out


def test_stats_reports_period_and_totals(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Сводка за период считает ходы, токены и стоимость (задача B7)."""
    for fixture in FIXTURES:
        (project / fixture.name).write_text(fixture.read_text())
    cli.main(["reindex"])
    capsys.readouterr()

    assert cli.main(["stats", "--period", "all"]) == 0
    out = capsys.readouterr().out
    assert "период       : all" in out
    assert "стоимость" in out


def test_stats_filters_by_project(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Фильтр по проекту ищет подстроку в slug, а не требует его целиком."""
    for fixture in FIXTURES:
        (project / fixture.name).write_text(fixture.read_text())
    cli.main(["reindex"])
    capsys.readouterr()

    assert cli.main(["stats", "--period", "all", "--project", "прое"]) == 0
    assert "проект ~ прое" in capsys.readouterr().out
    assert cli.main(["stats", "--period", "all", "--project", "нетакого"]) == 1
    assert "ходов нет" in capsys.readouterr().err


def test_period_is_parsed(project: Path) -> None:
    """Период понимает today, часы, дни, дату и «за всю историю»."""
    assert cli._since("all") is None
    assert cli._since("24h") is not None
    assert cli._since("7d") < cli._since("24h")  # type: ignore[operator]
    assert cli._since("2026-08-01").year == 2026
    with pytest.raises(SystemExit):
        cli._since("позавчера")


def test_unknown_command_is_reported(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["такого-нет"])


def test_serve_arguments_are_parsed() -> None:
    """Сервер здесь не поднимается — проверяется только разбор аргументов."""
    args = cli.build_parser().parse_args(["serve", "--port", "9999"])
    assert (args.command, args.port, args.host, args.reload) == ("serve", 9999, "127.0.0.1", False)


def test_serve_binds_localhost_by_default() -> None:
    """Инвариант ТЗ §7: наружу сервер не смотрит."""
    assert cli.build_parser().parse_args(["serve"]).host == "127.0.0.1"


# --- телеметрия (веха E) -----------------------------------------------------


def test_otel_env_points_at_the_dashboard(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Переменные окружения печатаются готовыми к вставке в профиль шелла."""
    assert cli.main(["otel", "--env", "--port", "9999"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "export CLAUDE_CODE_ENABLE_TELEMETRY=1" in lines
    assert "export OTEL_EXPORTER_OTLP_PROTOCOL=http/json" in lines
    assert "export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:9999/otlp" in lines


def test_otel_settings_fragment_is_valid_json(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Фрагмент для ~/.claude/settings.json — человек вставляет его руками:
    сами мы туда не пишем, каталог Claude Code открыт только на чтение."""
    assert cli.main(["otel", "--settings"]) == 0
    fragment = json.loads(capsys.readouterr().out)
    assert fragment["env"]["OTEL_METRICS_EXPORTER"] == "otlp"


def test_otel_status_explains_silence(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Пока телеметрия не включена, команда говорит об этом и подсказывает как."""
    assert cli.main(["otel"]) == 0
    out = capsys.readouterr().out
    assert "посылок не было" in out
    assert "cdash otel --env" in out


def test_otel_status_counts_what_arrived(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from cloudo_dash.collector import otlp

    with connect() as conn:
        otlp.ingest(
            conn,
            "logs",
            {
                "resourceLogs": [
                    {
                        "scopeLogs": [
                            {
                                "logRecords": [
                                    {
                                        "timeUnixNano": "1786690860000000000",
                                        "body": {"stringValue": "claude_code.api_request"},
                                        "attributes": [
                                            {
                                                "key": "session.id",
                                                "value": {"stringValue": "s1"},
                                            }
                                        ],
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
        )
    assert cli.main(["otel"]) == 0
    out = capsys.readouterr().out
    assert "logs" in out
    assert "событие api_request" in out
    assert "накоплено: 1 строк" in out  # объём и охват: видно, растёт ли база


def test_otel_prune_removes_old_records(project: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Срок хранения применяется и руками: события копятся быстрее ходов."""
    from cloudo_dash.collector import otlp

    with connect() as conn:
        otlp.store_events(
            conn,
            [
                otlp.EventRecord(
                    name="api_request", ts="2020-01-01T00:00:00.000000Z", session_id="s1", attrs={}
                )
            ],
        )
    assert cli.main(["otel", "--prune"]) == 0
    assert "убрано: метрик 0, событий 1" in capsys.readouterr().out
