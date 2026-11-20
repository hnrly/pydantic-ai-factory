"""Serve a factory: a queue of jobs in SQLite, a worker that does them, and a board.

This module is optional. ``SQLiteDispatcher`` and ``work`` import only the standard
library and the core, so a factory can be served without writing a queue.
``SQLiteDispatcher`` is one :class:`~pydantic_ai_factory.Dispatcher`; a deployment's own
queue, or a durable engine, may fill the same seam.

``board_app`` serves the factory as a board, a web page of cards moving across its
stations. It needs the ``[web]`` extra: ``pip install "pydantic-ai-factory[web]"``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic_ai.models import Model

from ..contracts import FactoryError
from ..factory import Dispatcher, Factory, Store
from ._sqlite import MINIMUM_SQLITE, STARTED_AGAIN, SQLiteDispatcher, SQLiteTooOld, utc_now
from ._worker import work

if TYPE_CHECKING:
    from ._app import FastAPI, Request

WEB_EXTRA = 'pip install "pydantic-ai-factory[web]"'


class BoardUnsupported(FactoryError):
    """The board cannot serve this factory, because its first station takes no mission.

    The board starts runs from a mission a person types, so the factory's first station
    must take a ``Mission`` or a ``str``.
    """


def board_app(
    build_factory: Callable[[Model | str], Factory],
    store: Store,
    dispatcher: Dispatcher,
    *,
    who: Callable[[Request], str | None] | None = None,
) -> FastAPI:
    """Serve a factory as a board: a FastAPI app with the API under ``/api`` and the page at ``/``.

    The web process never runs the factory, so it needs no model key. It builds the
    factory with ``build_factory(TestModel())`` only to read its shape and digest and to
    call its renderers. It reads runs from ``store``, and queues starts and answers on
    ``dispatcher`` for a worker, such as :func:`work`, to do.

    ``who`` reads the name of the person making a request, or ``None`` when there is
    none, which refuses the request with ``400``. By default it is ``typed_name``, which
    reads the name typed on the board from the ``X-Factory-Name`` header. Replace it to
    use a real sign-in.

    Raises :class:`BoardUnsupported` when the factory's first station takes neither a
    ``Mission`` nor a ``str``, and ``ImportError`` without the ``[web]`` extra.
    """
    try:
        from . import _app
    except ModuleNotFoundError as error:
        if error.name not in ("fastapi", "starlette") and not str(error.name).startswith(
            ("fastapi.", "starlette.")
        ):
            raise
        raise ImportError(
            f"board_app needs FastAPI, which comes with the [web] extra: {WEB_EXTRA}"
        ) from error
    return _app.board_app(build_factory, store, dispatcher, who=who)


__all__ = [
    "MINIMUM_SQLITE",
    "STARTED_AGAIN",
    "WEB_EXTRA",
    "BoardUnsupported",
    "SQLiteDispatcher",
    "SQLiteTooOld",
    "board_app",
    "utc_now",
    "work",
]
