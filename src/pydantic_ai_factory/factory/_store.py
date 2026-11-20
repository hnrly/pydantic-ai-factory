"""Where runs are kept: the run store protocol and its errors."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
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
        outcome: RunOutcome | Sequence[RunOutcome] | None = None,
        factory: str | None = None,
        order: Literal["newest", "oldest"] = "newest",
        limit: int = 50,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        before_run_id: str | None = None,
    ) -> list[Run]:
        """Return up to ``limit`` stored runs, sorted by ``updated_at``.

        Runs come newest first, or oldest first with ``order="oldest"``. Runs changed
        at the same moment are sorted by ``run_id`` the same way: last first when newest
        first. Each filter that is given must match: ``outcome`` is one outcome or
        several, ``factory`` a factory's name, and ``updated_after`` and
        ``updated_before`` keep only runs last saved strictly after or before that
        moment. Both moments must carry a time zone. ``before_run_id`` makes
        ``updated_before`` a cursor: it keeps runs whose ``(updated_at, run_id)`` comes
        before ``(updated_before, before_run_id)``, so paging back from the last run of
        one page loses no run saved at the same moment. Given without
        ``updated_before``, it raises ``ValueError``. ``limit`` must be between 1 and
        1000, or the store raises ``ValueError``. Each run is the latest one saved.
        """
        ...


class Store(ArtifactStore, RunStore, Protocol):
    """A store a factory can run with: it keeps both artifacts and runs."""
