"""What the browser chose for the native surfaces of the instrument.

The interface language is the browser's business, like the layout: it lives in
`localStorage` and the server does not decide it. But the menu-bar tray is the same
instrument's second surface, and `localStorage` is closed to it - so the choice is
mirrored into a file the native part can read. The mirror is one-way: the browser
writes it, the tray reads it, and nothing on the server takes its language from here.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import paths

#: The languages the interface dictionary knows (`web/src/lib/dict.ts`).
LANGUAGES = frozenset({"ru", "en"})


def load() -> dict[str, Any]:
    """The mirrored choice. An absent or broken file means "nobody has chosen yet"."""
    try:
        data = json.loads(paths.UI_STATE_PATH.read_text())
    except (OSError, ValueError):
        return {"lang": None}
    lang = data.get("lang") if isinstance(data, dict) else None
    return {"lang": lang if lang in LANGUAGES else None}


def save_lang(lang: str) -> None:
    """Write the language. The rename is atomic: the tray reads the file whenever it
    likes, and a half-written one would be broken JSON for it."""
    path = paths.UI_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps({"lang": lang}))
    os.replace(temporary, path)
