"""Where runs are kept: the run store protocol and its errors."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, Protocol

from ..artifacts import ArtifactStore
from ..contracts import FactoryError

if TYPE_CHECKING:
    from ._runner import Run, RunOutcome


class RunConflict(FactoryError):
    """The stored run has moved on since it was read, so nothing was saved.

    Two people answered the same run at once, or a run id was used twice. Load the run
    again with ``get_run`` and look at what it is waiting for now.
    """


class RunNotFound(FactoryError):
    """No run is stored under the id that was asked for."""


class RunStore(Protocol):
    """Keeps runs by their id, replacing each one only by compare-and-swap.

    ``put_run(run, replaces=previous)`` saves ``run`` only if the stored run is still
    ``previous``; with ``replaces=None`` the id must be new. Otherwise it raises
    :class:`RunConflict` and saves nothing. ``list_runs`` lists the stored runs, most
    recently changed first. ``pydantic_ai_factory.stores`` has ``encode_run`` and
    ``decode_run`` to build one.
    """

    async def get_run(self, run_id: str) -> Run:
        """Read the run stored under ``run_id``. Payloads in it are plain JSON data."""
        ...

    async def put_run(self, run: Run, *, replaces: Run | None) -> None:
        """Save ``run`` in place of ``replaces``, or raise :class:`RunConflict`."""
        ...

    async def list_runs(
        self,
        *,
        outcome: RunOutcome | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
    ) -> list[Run]:
        """Return up to ``limit`` stored runs, sorted by ``updated_at``.

        Runs come newest first, or oldest first with ``order="oldest"``. A run with no
        ``updated_at``, saved by 0.1, comes last in either order. Runs changed at the
        same moment come in ``run_id`` order. Only runs with this ``outcome`` and from
        this ``factory`` are listed, when either is given. ``limit`` must be between 1
        and 1000, or the store raises ``ValueError``. Each run is the latest one saved.
        """
        ...


class Store(ArtifactStore, RunStore, Protocol):
    """A store a factory can run with: it keeps both artifacts and runs."""
