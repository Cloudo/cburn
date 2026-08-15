"""Turn cost (task B1).

Rates are not hardcoded: they come from the `[prices]` config section into the
`model_prices` table, and the math runs in SQL over the four parts of usage - input,
output, cache reads and cache writes (5m and 1h are billed differently).
A model without a price costs zero: an honest zero beats an invented rate.
"""

from __future__ import annotations

import logging
import sqlite3
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: Prices are given per million tokens.
MTOK = 1_000_000

#: Rate template for `cburn prices --init`. It takes no part in the math -
#: it is copied into the user config, and the rates are edited by hand afterwards.
SAMPLE_PATH = Path(__file__).with_name("prices.sample.toml")

#: `model_prices` column -> key in the config's `[prices]` section.
_COLUMNS = {
    "in_per_mtok": "input",
    "out_per_mtok": "output",
    "cache_write_per_mtok": "cache_write_5m",
    "cache_write_1h_per_mtok": "cache_write_1h",
    "cache_read_per_mtok": "cache_read",
}

#: In the transcript a model may carry a date (`claude-haiku-4-5-20251001`), in the price
#: table it does not. The tail is dropped in SQL so that the join stays a single step.
_MODEL_KEY = """
    CASE WHEN turns.model GLOB '*-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]'
         THEN substr(turns.model, 1, length(turns.model) - 9)
         ELSE turns.model END
"""


def sync_prices(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    """Move `[prices]` from the config into `model_prices`; return the number of models.

    An empty section leaves the table alone: a config without prices must not wipe
    what was entered by hand.
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
    """Set `cost_usd` on turns: on all of them or only on the listed ones."""
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
    """Rebuild `sessions.cost_usd` from turns (after a price change)."""
    conn.execute(
        """
        UPDATE sessions SET cost_usd = COALESCE(
            (SELECT SUM(cost_usd) FROM turns WHERE turns.session_id = sessions.id), 0
        )
        """
    )


def recalculate(conn: sqlite3.Connection, config: dict[str, Any]) -> int:
    """Apply config prices to the whole history; return the number of priced models."""
    models = sync_prices(conn, config)
    with conn:
        apply_costs(conn)
        refresh_session_costs(conn)
    return models


def known_prices(conn: sqlite3.Connection) -> list[dict]:
    """What currently sits in `model_prices`."""
    return [dict(row) for row in conn.execute("SELECT * FROM model_prices ORDER BY model")]


def unknown_models(conn: sqlite3.Connection) -> list[dict]:
    """Models seen in turns that have no price: their spend counts as zero."""
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
    """Read the rate template (`cburn prices --init`)."""
    with SAMPLE_PATH.open("rb") as fh:
        return tomllib.load(fh).get("prices", {})


def _price_rows(prices: dict[str, Any]) -> Iterable[dict[str, Any]]:
    for model, values in prices.items():
        if not isinstance(values, dict):
            log.warning("price of model %s is not a table, skipping", model)
            continue
        row: dict[str, Any] = {"model": model}
        for column, key in _COLUMNS.items():
            try:
                row[column] = float(values.get(key, 0) or 0)
            except (TypeError, ValueError):
                log.warning("price %s.%s is not a number, treating as zero", model, key)
                row[column] = 0.0
        yield row
