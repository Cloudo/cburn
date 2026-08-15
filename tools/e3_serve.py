"""Check E3: the telemetry receiver on a separate port and its own database.

The production database is left alone - an ordinary dashboard already runs nearby.
"""

import sys
from pathlib import Path

import uvicorn

from cburn.api.server import create_app

db = Path(sys.argv[1])
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8801
app = create_app(db_path=db, watch=False, liveness=lambda: None)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
