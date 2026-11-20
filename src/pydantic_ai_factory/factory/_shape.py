"""What a factory looks like: its stations and rework rules, as plain data."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from ..contracts import Digest, Name, Record, digest_of
from ..stations._station import type_name

if TYPE_CHECKING:
    from ._factory import Factory


class GateShape(Record):
    """A station's gate: its name and version."""

    name: Name
    version: Name


class StationShape(Record):
    """One station, as the factory's digest sees it.

    ``input`` and ``output`` name the station's types. ``agents`` names the agents it
    may run, and ``methodologies`` the methodologies on them, as ``name@version``.
    ``approval`` says whether a person approves its work.
    """

    name: Name
    version: Name
    input: str
    output: str
    gate: GateShape | None
    agents: tuple[Name, ...]
    methodologies: tuple[str, ...]
    approval: bool


class ReworkShape(Record):
    """A rework rule: failed work at ``station`` goes back to ``to``, up to ``limit`` times."""

    station: Name
    to: Name
    limit: int
    on_limit: Literal["halt", "continue", "escalate"]


class FactoryShape(Record):
    """What a factory looks like: its stations in order and its rework rules.

    It holds what ``Factory.digest`` covers, and the digest itself, so a page can show
    the factory without importing its code.
    """

    name: Name
    version: Name
    digest: Digest
    stations: tuple[StationShape, ...]
    rework: tuple[ReworkShape, ...]


def described(factory: Factory) -> dict[str, Any]:
    """The data ``Factory.digest`` hashes. Changing it changes every factory's digest."""
    return {
        "name": factory.name,
        "version": factory.version,
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
            for s in factory.stations
        ],
        "rework": [
            [rule.station.name, rule.target.name, rule.limit, rule.on_limit]
            for rule in factory.rework
        ],
    }


def shape_of(factory: Factory) -> FactoryShape:
    """Build a factory's shape from the same data its digest hashes."""
    data = described(factory)
    return FactoryShape.model_validate(
        {
            **data,
            "digest": digest_of(data),
            "stations": [
                {
                    **station,
                    "gate": None
                    if station["gate"] is None
                    else {"name": station["gate"][0], "version": station["gate"][1]},
                }
                for station in data["stations"]
            ],
            "rework": [
                {"station": station, "to": to, "limit": limit, "on_limit": on_limit}
                for station, to, limit, on_limit in data["rework"]
            ],
        }
    )
