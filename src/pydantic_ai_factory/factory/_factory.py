"""Factories: the ordered stations that work moves through, with bounded rework."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic_ai import DeferredToolResults
from pydantic_ai.agent import AbstractAgent

from .._methodology import Methodology
from ..artifacts import ArtifactRef, ArtifactStore
from ..contracts import DefinitionError, check_name, digest_of, run_sync
from ..stations import Station
from ..stations._station import type_name
from ._mission import Accept, Declined, IntakeError, Mission, Signal, signal_artifact
from ._runner import Decision, OnStep, Run, resume_factory, run_factory
from ._store import Store

OnLimit = Literal["halt", "continue", "escalate"]


class FactoryDefinitionError(DefinitionError):
    """A factory was declared in a way that cannot work."""


@dataclass(frozen=True)
class Rework:
    """Send failed work back, a fixed number of times.

    When ``station`` fails its gate, the work goes back to ``to`` and runs forward again
    from there. ``to`` defaults to ``station`` itself. ``limit`` is how many times that
    may happen in one run; it is not reset when another rule sends the work back past
    this station. When the limit is reached, the run halts; with ``on_limit="continue"``
    it carries on to the next station, and with ``"escalate"`` it waits for a person.
    """

    station: Station[Any, Any]
    to: Station[Any, Any] | None = None
    limit: int = 1
    on_limit: OnLimit = "halt"

    @property
    def target(self) -> Station[Any, Any]:
        """Where work goes back to: ``to``, or the failing station itself."""
        return self.station if self.to is None else self.to


@dataclass(frozen=True)
class Factory:
    """A named, ordered list of stations, with the rules for sending work back.

    The order is the route. Each station's output is the next station's input, so their
    types must line up. A badly formed factory is refused when it is declared, with
    every finding at once.

    ``accept`` reads signals and returns a :class:`Mission`, or :class:`Declined`.
    :meth:`intake` calls it.

    A factory is a declaration. It holds no runs, no store and no other live state:
    :meth:`run` takes what one run needs and returns a record of it.
    """

    name: str
    stations: tuple[Station[Any, Any], ...]
    rework: tuple[Rework, ...] = ()
    accept: Accept | None = None
    version: str = "0"

    def __init__(
        self,
        name: str,
        stations: list[Station[Any, Any]] | tuple[Station[Any, Any], ...],
        *,
        rework: list[Rework] | tuple[Rework, ...] = (),
        accept: Accept | None = None,
        version: str = "0",
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "stations", tuple(stations))
        object.__setattr__(self, "rework", tuple(rework))
        object.__setattr__(self, "accept", accept)
        object.__setattr__(self, "version", version)
        findings = self._findings()
        if accept is not None and not callable(accept):
            findings.append(f"accept must be a function that reads signals, not {accept!r}")
        if findings:
            raise FactoryDefinitionError(f"factory {name!r}", findings)

    def index_of(self, station: str) -> int:
        """Return the position of the station called ``station``."""
        return [s.name for s in self.stations].index(station)

    def rework_for(self, station: str) -> Rework | None:
        """Return the rework rule that applies when the station ``station`` fails, if any."""
        return next((rule for rule in self.rework if rule.station.name == station), None)

    def agents(self) -> dict[str, tuple[AbstractAgent[Any, Any], ...]]:
        """Every agent each station may run, by station name, before anything runs."""
        return {station.name: station.agents for station in self.stations}

    def methodologies(self) -> dict[str, tuple[Methodology, ...]]:
        """Every methodology on each station's agents, by station name, before anything runs.

        Each is the methodology object itself; its ``ref`` is the name and version
        stamped on records.
        """
        return {station.name: station.methodologies for station in self.stations}

    @property
    def digest(self) -> str:
        """A digest of the factory's shape.

        It covers the stations in order, with their versions, types, gate versions,
        agents and methodologies, and the rework rules. A run records it, so a run
        that stopped can only be resumed on a factory of the same shape.
        """
        return digest_of(
            {
                "name": self.name,
                "version": self.version,
                "stations": [
                    {
                        "name": s.name,
                        "version": s.version,
                        "input": type_name(s.input_type),
                        "output": type_name(s.output_type),
                        "gate": [s.gate.name, s.gate.version] if s.gate else None,
                        "agents": [agent.name for agent in s.agents],
                        "methodologies": [str(m.ref) for m in s.methodologies],
                        "approval": s.approval,
                    }
                    for s in self.stations
                ],
                "rework": [
                    [rule.station.name, rule.target.name, rule.limit, rule.on_limit]
                    for rule in self.rework
                ],
            }
        )

    async def run(
        self,
        input: Any,
        *,
        store: Store,
        deps: Any = None,
        run_id: str | None = None,
        on_step: OnStep | None = None,
    ) -> Run:
        """Run ``input`` through the stations until the work finishes or has to stop.

        ``input`` is the first station's input. When that station takes a
        :class:`Mission` or a string, a plain string becomes a mission, and a station that
        takes a string is given ``mission.as_prompt()``. The mission is stored, named by
        ``run.mission_ref``, and every artifact the run makes carries its id. When a run
        of a mission made by :meth:`intake` ends, the factory stores an outcome signal and
        names it in ``run.outcome_signal``.

        The run stops early when a station did not run, when a station fails with no
        rework left, or when a person is needed. Rework happens only after a failure. A
        station that did not run is never retried.

        The run is saved in ``store`` after every step, so it can be loaded with
        ``store.get_run`` and resumed anywhere. ``on_step`` is awaited after each save,
        for watching progress.
        """
        return await run_factory(
            self,
            input,
            store=store,
            deps=deps,
            run_id=run_id,
            on_step=on_step,
        )

    async def resume(
        self,
        run: Run,
        answer: Decision | DeferredToolResults,
        *,
        store: Store,
        deps: Any = None,
        on_step: OnStep | None = None,
    ) -> Run:
        """Carry on a run that is waiting for a person, with that person's answer.

        ``answer`` is a decision from ``run.approve``, ``run.request_changes`` or
        ``run.reject``, or, when the run waits on tool calls, Pydantic AI's
        ``DeferredToolResults``. Approving moves the work on; on an escalation it accepts
        the work as it stands. Asking for changes sends the work back to the station once,
        with the note as feedback. Rejecting ends the run as failed.

        The run is carried on from ``run`` and the store alone, so it can be resumed in
        another process. Raises :class:`ApprovalRefused`, changing nothing, if the answer
        does not fit what the run waits for, and :class:`RunConflict` if the stored run
        has moved on since ``run`` was read.
        """
        return await resume_factory(self, run, answer, store=store, deps=deps, on_step=on_step)

    def resume_sync(
        self,
        run: Run,
        answer: Decision | DeferredToolResults,
        *,
        store: Store,
        deps: Any = None,
        on_step: OnStep | None = None,
    ) -> Run:
        """Carry on a waiting run, from code that is not async. See :meth:`resume`."""
        return run_sync(self.resume(run, answer, store=store, deps=deps, on_step=on_step))

    def run_sync(
        self,
        input: Any,
        *,
        store: Store,
        deps: Any = None,
        run_id: str | None = None,
        on_step: OnStep | None = None,
    ) -> Run:
        """Run ``input`` through the stations, from code that is not async. See :meth:`run`."""
        return run_sync(
            self.run(
                input,
                store=store,
                deps=deps,
                run_id=run_id,
                on_step=on_step,
            )
        )

    async def intake(
        self, signals: Sequence[Signal[Any]], *, store: ArtifactStore
    ) -> Mission | Declined:
        """Show ``signals`` to the factory's ``accept`` function and return what it decided.

        Each signal is stored first, whatever is decided. A mission that comes back
        records the refs of those signals in ``signals``, and so does a ``Declined``.
        Intake does not start a run: pass the mission to :meth:`run`.

        Raises :class:`IntakeError` if there are no signals, the factory has no
        ``accept`` function, or it returns something other than a mission or ``Declined``.
        An error raised by ``accept`` itself is passed on, and the signals stay stored.
        """
        if self.accept is None:
            raise IntakeError(f"factory {self.name!r} has no accept function; pass accept=")
        received = list(signals)
        if not received:
            raise IntakeError(f"factory {self.name!r}: intake needs at least one signal")
        strangers = [item for item in received if not isinstance(item, Signal)]  # pyright: ignore[reportUnnecessaryIsInstance]
        if strangers:
            raise IntakeError(
                f"factory {self.name!r}: intake takes signals, not "
                f"{type(strangers[0]).__name__}; wrap it in Signal(...)"
            )
        refs: tuple[ArtifactRef, ...] = tuple(
            [await store.put(signal_artifact(item)) for item in received]
        )
        decision: object = self.accept(received)
        if inspect.isawaitable(decision):
            decision = await decision
        if isinstance(decision, Mission):
            return decision.model_copy(update={"signals": refs})
        if isinstance(decision, Declined):  # pyright: ignore[reportUnnecessaryIsInstance]
            return decision.model_copy(update={"signals": refs})
        raise IntakeError(
            f"factory {self.name!r}: accept returned {type(decision).__name__}, "
            "not a Mission or Declined"
        )

    def intake_sync(
        self, signals: Sequence[Signal[Any]], *, store: ArtifactStore
    ) -> Mission | Declined:
        """Show signals to the factory, from code that is not async. See :meth:`intake`."""
        return run_sync(self.intake(signals, store=store))

    def _findings(self) -> list[str]:
        findings: list[str] = []
        for label, value in (("name", self.name), ("version", self.version)):
            problem = check_name(value)
            if problem:
                findings.append(f"the {label} {problem}")
        if not self.stations:
            findings.append("a factory needs at least one station")
        strangers = [item for item in self.stations if not isinstance(item, Station)]  # pyright: ignore[reportUnnecessaryIsInstance]
        if strangers:
            findings.append(f"every item must be a Station, not {strangers[0]!r}")
            return findings

        names = [station.name for station in self.stations]
        for name in sorted({n for n in names if names.count(n) > 1}):
            findings.append(f"station name {name!r} is used more than once")

        for before, after in zip(self.stations, self.stations[1:], strict=False):
            if not _hands_on(before.output_type, after.input_type):
                findings.append(
                    f"{before.name!r} hands on {type_name(before.output_type)} "
                    f"but {after.name!r} takes {type_name(after.input_type)}"
                )

        def position(station: object) -> int | None:
            return next((i for i, s in enumerate(self.stations) if s is station), None)

        seen: set[int] = set()
        for rule in self.rework:
            if not isinstance(rule, Rework):  # pyright: ignore[reportUnnecessaryIsInstance]
                findings.append(f"every rework rule must be a Rework, not {rule!r}")
                continue
            label = f"rework from {getattr(rule.station, 'name', rule.station)!r}"
            if type(rule.limit) is not int or rule.limit < 1:
                findings.append(f"{label}: the limit must be a whole number, at least 1")
            if rule.on_limit not in ("halt", "continue", "escalate"):
                findings.append(f"{label}: on_limit must be 'halt', 'continue' or 'escalate'")
            source = position(rule.station)
            if source is None:
                findings.append(f"{label}: the station is not in this factory")
                continue
            if source in seen:
                findings.append(f"{label}: declared more than once")
            seen.add(source)
            target = position(rule.target)
            if target is None:
                findings.append(f"{label}: the station it sends work to is not in this factory")
            elif target > source:
                findings.append(f"{label}: {rule.target.name!r} comes later in the factory")
            if rule.station.gate is None:
                findings.append(f"{label}: the station has no gate, so it can never fail")
        return findings


def _hands_on(output: Any, input: Any) -> bool:
    """Whether a station's output type can be the next station's input type."""
    if isinstance(output, type) and isinstance(input, type):
        return issubclass(output, input)
    return output == input
