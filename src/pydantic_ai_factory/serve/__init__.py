"""Serve a factory: a queue of jobs in SQLite, and a worker that does them.

This module is optional. It imports only the standard library and the core, so a
factory can be served without writing a queue. ``SQLiteDispatcher`` is one
:class:`~pydantic_ai_factory.Dispatcher`; a deployment's own queue, or a durable
engine, may fill the same seam.
"""

from ._sqlite import MINIMUM_SQLITE, STARTED_AGAIN, SQLiteDispatcher, SQLiteTooOld, utc_now
from ._worker import work

__all__ = [
    "MINIMUM_SQLITE",
    "STARTED_AGAIN",
    "SQLiteDispatcher",
    "SQLiteTooOld",
    "utc_now",
    "work",
]
