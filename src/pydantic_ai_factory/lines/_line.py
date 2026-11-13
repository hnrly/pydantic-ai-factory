"""Lines: an ordered route through stations, with bounded rework."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from ..contracts import FactoryError
from ..stations import Station

OnLimit = Literal["halt", "continue"]


class LineDefinitionError(FactoryError):
    """A line was declared in a way that cannot work.

    ``findings`` lists every problem, so they can all be fixed in one go.
    """

    def __init__(self, line: str, findings: list[str]) -> None:
        self.line = line
        self.findings = findings
        super().__init__(f"line {line!r} is not valid: " + "; ".join(findings))


@dataclass(frozen=True)
class Rework:
    """Send failed work back, a fixed number of times.

    When ``from_station`` fails its gate, the line goes back to ``to_station`` and runs
    forward again from there. ``limit`` is how many times that may happen. When the
    limit is reached, the line halts, or carries on to the next station if ``on_limit``
    is ``"continue"``.
    """

    from_station: str
    to_station: str | None = None
    limit: int = 1
    on_limit: OnLimit = "halt"

    @property
    def target(self) -> str:
        """Where work goes back to: ``to_station``, or the failing station itself."""
        return self.to_station or self.from_station


@dataclass(frozen=True)
class Line:
    """An ordered route through stations.

    Each station's output is the next station's input, so their types must line up.
    A badly formed line is refused when it is declared, with every finding at once.
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
            raise LineDefinitionError(name, findings)

    def index_of(self, station: str) -> int:
        """Return the position of the station called ``station``."""
        return [s.name for s in self.stations].index(station)

    def rework_for(self, station: str) -> Rework | None:
        """Return the rework rule that applies when ``station`` fails, if any."""
        return next((rule for rule in self.rework if rule.from_station == station), None)

    def _findings(self) -> list[str]:
        findings: list[str] = []
        if not self.name.strip() or self.name != self.name.strip():
            findings.append("the name must be non-empty with no surrounding space")
        if not self.stations:
            findings.append("a line needs at least one station")

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
            label = f"rework from {rule.from_station!r}"
            if rule.from_station not in names:
                findings.append(f"{label}: no such station")
                continue
            if rule.from_station in seen:
                findings.append(f"{label}: declared more than once")
            seen.add(rule.from_station)
            if rule.target not in names:
                findings.append(f"{label}: target {rule.target!r} is not a station")
            elif names.index(rule.target) > names.index(rule.from_station):
                findings.append(f"{label}: target {rule.target!r} comes later in the line")
            if rule.limit < 1:
                findings.append(f"{label}: the limit must be at least 1")
            if self.stations[names.index(rule.from_station)].gate is None:
                findings.append(f"{label}: the station has no gate, so it can never fail")
        return findings
