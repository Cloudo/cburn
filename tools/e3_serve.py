"""Сверка E3: приёмник телеметрии на отдельном порту и своей БД.

Боевая база при этом не трогается — рядом уже работает обычный дашборд.
"""

import sys
from pathlib import Path

import uvicorn

from cloudo_dash.api.server import create_app

db = Path(sys.argv[1])
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8801
app = create_app(db_path=db, watch=False, liveness=lambda: None)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
