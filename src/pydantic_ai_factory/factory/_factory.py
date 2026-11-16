"""Factories: the ordered stations that work moves through, with bounded rework."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from ..artifacts import Artifact, ArtifactStore
from ..contracts import DefinitionError, MethodologyRef, check_name, digest_of
from ..stations import Station
from ._runner import OnStep, Run, run_factory

OnLimit = Literal["halt", "continue"]


class FactoryDefinitionError(DefinitionError):
    """A factory was declared in a way that cannot work."""


@dataclass(frozen=True)
class Rework:
    """Send failed work back, a fixed number of times.

    When ``from_station`` fails its gate, the work goes back to ``to_station`` and runs
    forward again from there. ``limit`` is how many times that may happen in one run; it
    is not reset when another rule sends the work back past this station. When the limit
    is reached, the run halts, or carries on to the next station if ``on_limit`` is
    ``"continue"``.
    """

    from_station: str
    to_station: str | None = None
    limit: int = 1
    on_limit: OnLimit = "halt"

    @property
    def target(self) -> str:
        """Where work goes back to: ``to_station``, or the failing station itself."""
        return self.from_station if self.to_station is None else self.to_station


@dataclass(frozen=True)
class Factory:
    """A named, ordered list of stations, with the rules for sending work back.

    The order is the route. Each station's output is the next station's input, so their
    types must line up. A badly formed factory is refused when it is declared, with
    every finding at once.

    A factory is a declaration. It holds no runs, no store and no other live state:
    :meth:`run` takes what one run needs and returns a record of it.
    """

    name: str
    stations: tuple[Station[Any, Any], ...]
    rework: tuple[Rework, ...] = ()
    version: str = "0"

    def __init__(
        self,
        name: str,
        stations: list[Station[Any, Any]] | tuple[Station[Any, Any], ...],
        *,
        rework: list[Rework] | tuple[Rework, ...] = (),
        version: str = "0",
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "stations", tuple(stations))
        object.__setattr__(self, "rework", tuple(rework))
        object.__setattr__(self, "version", version)
        findings = self._findings()
        if findings:
            raise FactoryDefinitionError(f"factory {name!r}", findings)

    def index_of(self, station: str) -> int:
        """Return the position of the station called ``station``."""
        return [s.name for s in self.stations].index(station)

    def rework_for(self, station: str) -> Rework | None:
        """Return the rework rule that applies when ``station`` fails, if any."""
        return next((rule for rule in self.rework if rule.from_station == station), None)

    @property
    def digest(self) -> str:
        """A digest of the factory's shape.

        It covers the stations in order, with their versions, types and gate versions,
        and the rework rules. A run records it, so a run that stopped can only be
        resumed on a factory of the same shape.
        """
        return digest_of(
            {
                "name": self.name,
                "version": self.version,
                "stations": [
                    {
                        "name": s.name,
                        "version": s.version,
                        "input": s.input_type.__name__,
                        "output": s.output_type.__name__,
                        "gate": [s.gate.name, s.gate.version] if s.gate else None,
                    }
                    for s in self.stations
                ],
                "rework": [
                    [rule.from_station, rule.target, rule.limit, rule.on_limit]
                    for rule in self.rework
                ],
            }
        )

    async def run(
        self,
        input: BaseModel | Artifact[Any],
        *,
        store: ArtifactStore,
        deps: Any = None,
        run_id: str | None = None,
        mission_id: str | None = None,
        methodologies: Sequence[MethodologyRef] = (),
        on_step: OnStep | None = None,
    ) -> Run:
        """Run ``input`` through the stations until the work finishes or has to stop.

        The run stops early when a station did not run, when a station fails with no
        rework left, or when a gate asks for a person. Rework happens only after a
        failure. A station that did not run is never retried.

        ``on_step`` is awaited after every attempt with the record so far, whose outcome
        is ``"running"``. Use it to save progress: if the store raises, or the run is
        cancelled, no record is returned.
        """
        return await run_factory(
            self,
            input,
            store=store,
            deps=deps,
            run_id=run_id,
            mission_id=mission_id,
            methodologies=methodologies,
            on_step=on_step,
        )

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
            if not issubclass(before.output_type, after.input_type):
                findings.append(
                    f"{before.name!r} hands on {before.output_type.__name__} "
                    f"but {after.name!r} takes {after.input_type.__name__}"
                )

        seen: set[str] = set()
        for rule in self.rework:
            if not isinstance(rule, Rework):  # pyright: ignore[reportUnnecessaryIsInstance]
                findings.append(f"every rework rule must be a Rework, not {rule!r}")
                continue
            label = f"rework from {rule.from_station!r}"
            if type(rule.limit) is not int or rule.limit < 1:
                findings.append(f"{label}: the limit must be a whole number, at least 1")
            if rule.on_limit not in ("halt", "continue"):
                findings.append(f"{label}: on_limit must be 'halt' or 'continue'")
            if rule.from_station not in names:
                findings.append(f"{label}: no such station")
                continue
            if rule.from_station in seen:
                findings.append(f"{label}: declared more than once")
            seen.add(rule.from_station)
            if rule.target not in names:
                findings.append(f"{label}: target {rule.target!r} is not a station")
            elif names.index(rule.target) > names.index(rule.from_station):
                findings.append(f"{label}: target {rule.target!r} comes later in the factory")
            if self.stations[names.index(rule.from_station)].gate is None:
                findings.append(f"{label}: the station has no gate, so it can never fail")
        return findings
