"""Стоимость ходов (задача B1).

Тарифы в коде не зашиты: они приходят из секции `[prices]` конфига в таблицу
`model_prices`, а расчёт идёт в SQL по четырём составляющим usage — вход,
выход, чтение кэша и запись в кэш (5m и 1h тарифицируются по-разному).
Модель без цены стоит ноль: лучше честный ноль, чем выдуманный тариф.
"""

from __future__ import annotations

import logging
import sqlite3
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Цены задаются за миллион токенов.
MTOK = 1_000_000

#: Заготовка тарифов для `cdash prices --init`. В расчёте не участвует —
#: копируется в пользовательский конфиг, дальше цены редактирует человек.
SAMPLE_PATH = Path(__file__).with_name("prices.sample.toml")

#: Колонка `model_prices` -> ключ в секции `[prices]` конфига.
_COLUMNS = {
    "in_per_mtok": "input",
    "out_per_mtok": "output",
    "cache_write_per_mtok": "cache_write_5m",
    "cache_write_1h_per_mtok": "cache_write_1h",
    "cache_read_per_mtok": "cache_read",
}

#: В транскрипте модель бывает с датой (`claude-haiku-4-5-20251001`), в прайсе
#: она без неё. Хвост отбрасывается прямо в SQL, чтобы join остался одним шагом.
_MODEL_KEY = """
    CASE WHEN turns.model GLOB '*-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
         THEN substr(turns.model, 1, length(turns.model) - 9)
         ELSE turns.model END
"""


def sync_prices(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    """Перенести `[prices]` из конфига в `model_prices`; вернуть число моделей.

    Пустая секция таблицу не трогает: конфиг без цен не должен обнулять то,
    что уже заведено руками.
    """
    rows = list(_price_rows(config.get("prices") or {}))
    if not rows:
        return 0
    with conn:
        conn.execute("DELETE FROM model_prices")
        conn.executemany(
            f"""
            INSERT INTO model_prices (model, {", ".join(_COLUMNS)})
            VALUES (:model, {", ".join(f":{column}" for column in _COLUMNS)})
            """,  # noqa: S608
            rows,
        )
    return len(rows)


def apply_costs(conn: sqlite3.Connection, message_ids: Iterable[str] | None = None) -> int:
    """Проставить `cost_usd` ходам: всем или только перечисленным."""
    formula = f"""
        COALESCE((
            SELECT (turns.input_tokens   * p.in_per_mtok
                  + turns.output_tokens  * p.out_per_mtok
                  + turns.cache_read     * p.cache_read_per_mtok
                  + turns.cache_write_5m * p.cache_write_per_mtok
                  + turns.cache_write_1h * p.cache_write_1h_per_mtok) / {MTOK}.0
              FROM model_prices AS p
             WHERE p.model = ({_MODEL_KEY})
        ), 0)
    """
    if message_ids is None:
        cursor = conn.execute(f"UPDATE turns SET cost_usd = {formula}")  # noqa: S608
        return cursor.rowcount
    ids = tuple(message_ids)
    if not ids:
        return 0
    cursor = conn.execute(
        f"""
        UPDATE turns SET cost_usd = {formula}
         WHERE message_id IN ({",".join("?" * len(ids))})
        """,  # noqa: S608
        ids,
    )
    return cursor.rowcount


def refresh_session_costs(conn: sqlite3.Connection) -> None:
    """Пересобрать `sessions.cost_usd` из ходов (после смены цен)."""
    conn.execute(
        """
        UPDATE sessions SET cost_usd = COALESCE(
            (SELECT SUM(cost_usd) FROM turns WHERE turns.session_id = sessions.id), 0
        )
        """
    )


def recalculate(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    """Применить цены из конфига ко всей истории; вернуть число моделей в прайсе."""
    models = sync_prices(conn, config)
    with conn:
        apply_costs(conn)
        refresh_session_costs(conn)
    return models


def known_prices(conn: sqlite3.Connection) -> list[dict]:
    """Что сейчас лежит в `model_prices`."""
    return [dict(row) for row in conn.execute("SELECT * FROM model_prices ORDER BY model")]


def unknown_models(conn: sqlite3.Connection) -> list[dict]:
    """Модели из ходов, для которых цены нет: их расход считается нулём."""
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT ({_MODEL_KEY}) AS model, COUNT(*) AS turns
              FROM turns
             WHERE turns.model IS NOT NULL
               AND ({_MODEL_KEY}) NOT IN (SELECT model FROM model_prices)
             GROUP BY 1
             ORDER BY turns DESC
            """  # noqa: S608
        )
    ]


def sample_prices() -> dict[str, Any]:
    """Прочитать заготовку тарифов (`cdash prices --init`)."""
    with SAMPLE_PATH.open("rb") as fh:
        return tomllib.load(fh).get("prices", {})


def _price_rows(prices: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for model, values in prices.items():
        if not isinstance(values, dict):
            log.warning("цена модели %s задана не таблицей, пропускаем", model)
            continue
        row: dict[str, Any] = {"model": model}
        for column, key in _COLUMNS.items():
            try:
                row[column] = float(values.get(key, 0) or 0)
            except (TypeError, ValueError):
                log.warning("цена %s.%s не число, считаем нулём", model, key)
                row[column] = 0.0
        yield row
