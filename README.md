# claude-speedo

Локальный «спидометр» для Claude Code: следит за всеми сессиями на машине,
показывает расход токенов в реальном времени и раз в час предлагает конкретные
оптимизации (что вынести в скилл, где поправить permissions, какой MCP отключить,
где пора делать `/clear`).

Полное техническое задание — [TZ.md](TZ.md).

## Статус

Этап M1 (CLI-прототип: парсер JSONL, SQLite, `speedo stats/sessions/session`) — в работе.
Сейчас в репозитории каркас: схема БД, конфиг, CLI, дымовые тесты.

## Установка для разработки

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Команды

```bash
speedo paths      # где лежат конфиг, БД и транскрипты
speedo initdb     # создать БД и применить схему
speedo stats      # M1
speedo sessions   # M1
speedo serve      # M2, дашборд на http://localhost:8799
```

## Проверки

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
```

## Приватность

`~/.claude` открывается строго на чтение. Содержимое переписки не покидает машину:
наружу уходят только агрегированный дайджест в `claude -p` и текст уведомлений
в telegram.
