"""The app's settings, read once from the environment, with defaults.

- ``FACTORY_DB``: the SQLite file both processes share. Default ``factory.db``.
- ``FACTORY_STALLED_MINUTES``: how long a running run or a claimed job may go without a
  change before it is shown as stalled. Default 10.
- ``FACTORY_MODEL``: the Pydantic AI model the worker uses, such as
  ``anthropic:claude-opus-5-5``. Only the worker reads it. Unset by default.
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

DB: Path = Path(os.environ.get("FACTORY_DB", "factory.db"))
STALLED_AFTER: timedelta = timedelta(minutes=float(os.environ.get("FACTORY_STALLED_MINUTES", "10")))
MODEL: str | None = os.environ.get("FACTORY_MODEL") or None
