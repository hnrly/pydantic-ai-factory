"""Reading the store and the dispatcher, and carding what was read.

:class:`BoardReader` is frozen: it holds the factory and where its work is kept, never
what it read. Each method reads afresh, so one reader serves every request.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, NamedTuple

from ...factory import Dispatcher, Factory, Job, JobState, Mission, Run, RunNotFound, Store
from .cards import first_line, link_restarts, run_card, start_card
from .views import Card

JOBS_MAX = 1000
"""How many jobs one ``list_jobs`` call returns."""

RUNS_MAX = 1000
"""How many runs one ``list_runs`` call returns."""

IDS_MAX = 500
"""How many run ids one ``list_jobs`` call names."""


class Found(NamedTuple):
    """One run's card, and what was read to make it, so a request reads each thing once.

    ``jobs`` are the run's jobs that could drive it or answer it, ``start`` the job that
    queued its mission, and ``restart`` the job that started it again, where there is one.
    """

    card: Card
    run: Run | None
    start: Job | None
    jobs: list[Job]
    restart: Job | None


@dataclass(frozen=True)
class BoardReader:
    """Reads one factory's runs from ``store`` and its jobs from ``dispatcher``."""

    factory: Factory
    store: Store
    dispatcher: Dispatcher

    # Runs.

    async def runs(self, **filters: Any) -> list[Run]:
        """Every run of this factory that matches, newest change first."""
        found: list[Run] = []
        cursor: dict[str, Any] = {}
        while True:
            page = await self.store.list_runs(
                factory=self.factory.name, limit=RUNS_MAX, **filters, **cursor
            )
            found.extend(page)
            if len(page) < RUNS_MAX:
                return found
            last = page[-1]
            cursor = {"updated_before": last.updated_at, "before_run_id": last.run_id}

    async def saved(self, run_id: str) -> Run | None:
        """The run ``run_id`` of this factory, or ``None`` when there is none."""
        try:
            run = await self.store.get_run(run_id)
        except RunNotFound:
            return None
        return run if run.factory == self.factory.name else None

    async def mission_of(self, run: Run) -> Mission | None:
        if run.mission_ref is None:
            return None
        payload: Any = (await self.store.get(run.mission_ref, Mission)).payload
        return payload if isinstance(payload, Mission) else Mission.model_validate(payload)

    # Jobs.

    async def scan_jobs(
        self, before: tuple[datetime, str] | None = None, **filters: Any
    ) -> AsyncIterator[Job]:
        """Jobs that match, newest change first, page by page, each once."""
        seen: set[str] = set()
        bound = None if before is None else before[0] + timedelta(microseconds=1)
        while True:
            page = await self.dispatcher.list_jobs(limit=JOBS_MAX, updated_before=bound, **filters)
            fresh = [job for job in page if job.job_id not in seen]
            for job in fresh:
                seen.add(job.job_id)
                yield job
            if len(page) < JOBS_MAX or not fresh:
                return
            bound = page[-1].updated_at + timedelta(microseconds=1)

    async def open_jobs(self) -> list[Job]:
        """Every queued or claimed job, however many there are."""
        return [job async for job in self.scan_jobs(state=("queued", "claimed"))]

    async def start_job_of(self, run_id: str) -> Job | None:
        found = await self.dispatcher.list_jobs(kind="start", run_id=run_id, limit=1)
        return found[0] if found else None

    async def restarts(self, run_ids: Iterable[str]) -> dict[str, Job]:
        """The job that started each of ``run_ids`` again, where one did."""
        ids = sorted(set(run_ids))
        links: dict[str, Job] = {}
        for at in range(0, len(ids), IDS_MAX):
            chunk = ids[at : at + IDS_MAX]
            for job in await self.dispatcher.list_jobs(restart_of=chunk, limit=JOBS_MAX):
                if job.restart_of is not None:
                    links[job.restart_of] = job
        return links

    async def jobs_for(
        self, live: Sequence[Run], opened: Sequence[Job] | None = None
    ) -> dict[str, list[Job]]:
        """Each run's jobs that could be driving it, or queue an answer to it.

        Open jobs are always read, every page of them, unless ``opened`` already holds
        them. Each live run's newest job is read too, by its run id, so no number of
        newer jobs for other runs can hide it.
        """
        found: list[Job] = list(await self.open_jobs() if opened is None else opened)
        ids = sorted({run.run_id for run in live})
        for at in range(0, len(ids), IDS_MAX):
            found += await self.dispatcher.list_jobs(
                latest_per_run=True, run_id=ids[at : at + IDS_MAX], limit=JOBS_MAX
            )
        by_run: dict[str, dict[str, Job]] = {}
        for job in found:
            if not job.refused:
                by_run.setdefault(job.run_id, {})[job.job_id] = job
        return {run_id: list(jobs.values()) for run_id, jobs in by_run.items()}

    # Start jobs whose run was never saved.

    async def saved_ids(self, starts: Sequence[Job], known: set[str]) -> set[str]:
        """The run ids of ``starts`` that have a saved run of this factory.

        Ids in ``known`` are saved. The rest are read together, as the runs saved since
        the oldest of those jobs was queued, never one read per job.
        """
        found = {job.run_id for job in starts if job.run_id in known}
        unknown = [job for job in starts if job.run_id not in known]
        if not unknown:
            return found
        # A start job's run is saved after the job is queued; a second allows for clocks.
        floor = min(job.created_at for job in unknown) - timedelta(seconds=1)
        wanted = {job.run_id for job in unknown}
        return found | {run.run_id for run in await self.runs(updated_after=floor)} & wanted

    async def unsaved_starts(
        self,
        states: Sequence[JobState],
        before: tuple[datetime, str] | None,
        want: int,
        now: datetime,
        *,
        known: set[str],
        oldest: datetime | None = None,
    ) -> list[Job]:
        """Up to ``want`` start jobs in ``states`` whose run was never saved, newest first.

        Only those carded in Stopped when ``"queued"`` is not among ``states``. A start
        job that is done saved its run, so ``states`` never needs ``"done"``. The scan
        stops at jobs last changed before ``oldest``, the oldest card the page can show.
        ``known`` holds run ids already read as saved.
        """
        found: list[Job] = []
        batch: list[Job] = []

        async def keep_unsaved() -> None:
            saved_now = await self.saved_ids(batch, known)
            found.extend(job for job in batch if job.run_id not in saved_now)
            batch.clear()

        async for job in self.scan_jobs(before, kind="start", state=tuple(states)):
            if oldest is not None and job.updated_at < oldest:
                break
            if before is not None and (job.updated_at, job.run_id) >= before:
                continue
            if "queued" not in states and job.state == "claimed" and not job.stalled(now):
                continue
            batch.append(job)
            if len(found) + len(batch) >= want:
                await keep_unsaved()
                if len(found) >= want:
                    break
        await keep_unsaved()
        return found[:want]

    # Cards.

    async def cards(
        self,
        found: Iterable[Run],
        starts: Iterable[Job],
        now: datetime,
        jobs: dict[str, list[Job]] | None = None,
        links: dict[str, Job] | None = None,
    ) -> list[Card]:
        """Card every run and every start job with no saved run, each run once.

        ``jobs`` is what :meth:`jobs_for` returned for the live runs among ``found``, and
        ``links`` what :meth:`restarts` returned for every card, when the caller read them
        already.
        """
        unique: dict[str, Run] = {}
        for run in found:
            if run.factory == self.factory.name:
                unique.setdefault(run.run_id, run)
        unsaved = {job.run_id: job for job in starts if job.run_id not in unique}
        if jobs is None:
            live = [run for run in unique.values() if run.outcome in ("running", "waiting")]
            jobs = await self.jobs_for(live)
        made: list[Card] = []
        missions: dict[str, str] = {}
        for run in unique.values():
            key = run.mission_id or ""
            if key not in missions:
                mission = await self.mission_of(run)
                missions[key] = first_line(mission.objective) if mission else ""
            made.append(run_card(self.factory, run, missions[key], jobs.get(run.run_id, []), now))
        made += [start_card(job, now) for job in unsaved.values()]
        if links is None:
            links = await self.restarts(card.run_id for card in made)
        link_restarts(made, links)
        return made

    async def card_of(self, run_id: str, now: datetime) -> Found | None:
        """One card, read once, with what was read to make it, or ``None`` if there is none."""
        links = await self.restarts([run_id])
        restart = links.get(run_id)
        run = await self.saved(run_id)
        if run is not None:
            jobs = (await self.jobs_for([run])).get(run_id, [])
            # The board cards a run that has ended from its open jobs alone; so does this.
            live = run.outcome in ("running", "waiting")
            shown = jobs if live else [job for job in jobs if job.state in ("queued", "claimed")]
            card = (await self.cards([run], (), now, {run_id: shown}, links))[0]
            start = next((job for job in jobs if job.kind == "start"), None)
            if start is None:
                start = await self.start_job_of(run_id)
            return Found(card, run, start, jobs, restart)
        job = await self.start_job_of(run_id)
        if job is not None:
            card = (await self.cards((), [job], now, {}, links))[0]
            return Found(card, None, job, [job], restart)
        return None
