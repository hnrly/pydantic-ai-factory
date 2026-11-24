"""The board as the page loads and polls it, and the pages of Done, Stopped and every card.

A full load cards every live run and every open start, with a page of Done and of
Stopped. A poll since a moment cards only what changed. "See all" pages back through a
column, newest change first, with a cursor of ``updated_at,run_id``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from ...factory import FactoryShape, Job, Run
from .reading import BoardReader
from .views import Board, Card, CardPage

PAGE = 50
"""How many cards ``GET /api/runs`` returns at a time."""

SHOWN = 20
"""How many cards Done and Stopped show on a full load of the board."""

OVERLAP = timedelta(seconds=10)
"""How far before a poll's start the next poll looks, so no save committed during it is lost."""

Cursor = tuple[datetime, str]


class BadCursor(ValueError):
    """A ``before`` cursor that is not an ``updated_at`` with a time zone, a comma and a run id."""


def parse_cursor(before: str | None) -> Cursor | None:
    """Read a ``before`` cursor, as :func:`cursor_of` writes it. Raises :class:`BadCursor`."""
    if before is None:
        return None
    moment, comma, run_id = before.partition(",")
    try:
        when = datetime.fromisoformat(moment)
    except ValueError:
        when = None
    if not comma or not run_id or when is None or when.utcoffset() is None:
        raise BadCursor("before must be an updated_at with a time zone, a comma, and a run id")
    return (when, run_id)


def cursor_of(card: Card) -> str:
    return f"{card.updated_at.isoformat()},{card.run_id}"


def _key(card: Card) -> Cursor:
    return (card.updated_at, card.run_id)


def _cut(found: list[Card], before: Cursor | None, size: int) -> CardPage:
    unique = {card.run_id: card for card in found}
    ordered = sorted(
        (card for card in unique.values() if before is None or _key(card) < before),
        key=_key,
        reverse=True,
    )
    shown = ordered[:size]
    more = len(ordered) > size
    return CardPage(cards=shown, before=cursor_of(shown[-1]) if more and shown else None)


def _after(before: Cursor | None) -> dict[str, Any]:
    if before is None:
        return {}
    return {"updated_before": before[0], "before_run_id": before[1]}


def _oldest_shown(found: Sequence[Run], size: int) -> datetime | None:
    """When the oldest card a page can show last changed, once ``found`` fills it.

    ``found`` is newest first, and every run in it is carded on the page.
    """
    return found[size - 1].updated_at if len(found) >= size else None


async def done_page(
    reader: BoardReader, before: Cursor | None, size: int, now: datetime
) -> CardPage:
    found = await reader.store.list_runs(
        outcome="passed", factory=reader.factory.name, limit=size + 1, **_after(before)
    )
    # A passed run is carded by rules 1 and 2, which never look at its jobs.
    return _cut(await reader.cards(found, (), now, {}), before, size)


async def stopped_page(
    reader: BoardReader,
    before: Cursor | None,
    size: int,
    now: datetime,
    live: Sequence[Run] | None = None,
    jobs: dict[str, list[Job]] | None = None,
) -> CardPage:
    """A page of Stopped. ``live`` and ``jobs`` are the live runs and their jobs, when
    the caller read them already."""
    # Three sources, each newest change first; the card rules give each one state.
    ended = await reader.store.list_runs(
        outcome=("failed", "did_not_run"),
        factory=reader.factory.name,
        limit=size + 1,
        **_after(before),
    )
    if live is None:
        live = await reader.runs(outcome=("running", "waiting"))
    starts = await reader.unsaved_starts(
        ("failed", "claimed"),
        before,
        size + 1,
        now,
        known={run.run_id for run in [*ended, *live]},
        oldest=_oldest_shown(ended, size),
    )
    found = await reader.cards([*ended, *live], starts, now, jobs)
    return _cut([card for card in found if card.column == "stopped"], before, size)


async def all_page(
    reader: BoardReader, before: Cursor | None, size: int, now: datetime
) -> CardPage:
    found = await reader.store.list_runs(
        factory=reader.factory.name, limit=size + 1, **_after(before)
    )
    starts = await reader.unsaved_starts(
        ("queued", "claimed", "failed"),
        before,
        size + 1,
        now,
        known={run.run_id for run in found},
        oldest=_oldest_shown(found, size),
    )
    return _cut(await reader.cards(found, starts, now), before, size)


async def full_board(reader: BoardReader, shape: FactoryShape, now: datetime) -> Board:
    """Every live run, every open start, and a page of Done and of Stopped."""
    live = await reader.runs(outcome=("running", "waiting"))
    opened = await reader.open_jobs()
    starts = [job for job in opened if job.kind == "start"]
    jobs = await reader.jobs_for(live, opened)
    found = await reader.cards(live, starts, now, jobs)
    done = await done_page(reader, None, SHOWN, now)
    stopped = await stopped_page(reader, None, SHOWN, now, live, jobs)
    merged = {card.run_id: card for card in [*found, *done.cards, *stopped.cards]}
    return Board(
        shape=shape,
        cards=list(merged.values()),
        since=now - OVERLAP,
        more={"done": done.before is not None, "stopped": stopped.before is not None},
    )


async def board_since(
    reader: BoardReader, shape: FactoryShape, since: datetime, now: datetime
) -> Board:
    """Every card that changed since ``since``, which must carry a time zone."""
    live = await reader.runs(outcome=("running", "waiting"))
    opened = await reader.open_jobs()
    changed = await reader.runs(updated_after=since)
    # Every job that changed, not just each run's newest, so that a restart, which
    # closes the old run's open job, re-cards the old run.
    moved = [job async for job in reader.scan_jobs(updated_after=since)]
    known = {run.run_id for run in [*live, *changed]}
    start_of = {job.run_id: job for job in [*moved, *opened] if job.kind == "start"}
    # A run started again since gets its card back, so it loses "Start again"
    # wherever the board is open, even when nothing about the old run changed.
    old = [job.restart_of for job in moved if job.restart_of is not None]
    extra: list[Run] = []
    starts: list[Job] = []
    for run_id in dict.fromkeys([*(job.run_id for job in [*moved, *opened]), *old]):
        if run_id in known:
            continue
        known.add(run_id)
        run = await reader.saved(run_id)
        start = start_of.get(run_id) or (
            await reader.start_job_of(run_id) if run is None and run_id in old else None
        )
        if run is not None:
            extra.append(run)
        elif start is not None:
            starts.append(start)
    every = [*live, *changed, *extra]
    current = [run for run in every if run.outcome in ("running", "waiting")]
    found = await reader.cards(every, starts, now, await reader.jobs_for(current, opened))
    return Board(shape=shape, cards=found, since=now - OVERLAP)
