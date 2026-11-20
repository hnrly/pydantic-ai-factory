"""Serve the software factory as a board.

Run the web process with `uvicorn board:app`. It needs no model key.
Run the worker with `python board.py`, on Claude, or `python . --offline` with no key.
"""

import os

from pydantic_ai_factory import SQLiteStore
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, work
from pydantic_ai_factory_software import SoftwareFactory

DB = os.environ.get("FACTORY_DB", "factory.db")
store = SQLiteStore(DB)
dispatcher = SQLiteDispatcher(DB)

app = board_app(SoftwareFactory, store, dispatcher)

if __name__ == "__main__":
    work(SoftwareFactory, store, dispatcher, model="anthropic:claude-opus-5-5")
