"""The software factory: a mission in, an approved design out. More stations follow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, FactoryDefinitionError, Rework, Station
from pydantic_ai_factory.factory import Accept

from ._design import Design
from ._station import design_station


@dataclass(frozen=True, init=False, eq=False, kw_only=True)
class SoftwareFactory(Factory):
    """A ready-made factory for building software. Today it has one station, Design.

    Run it with a mission, or a string that becomes one. ``design`` is its Design
    station; by default :func:`design_station` ``(model)``, where a designer agent drafts a
    :class:`Design` under :func:`design_review`, Elephant Goldfish tuned for software
    designs. A design a builder could not follow, such as one with open questions or an
    acceptance criterion no check proves, fails :func:`design_is_buildable` and is sent
    back to the designer up to ``repairs`` times; after that a person decides. A design
    that passes waits for a person to approve it. Before drafting, or during review, the
    designer may ask you questions with :class:`~pydantic_ai_factory.AskPerson`.

    ``workspace``, a capability that supplies one such as ``LocalWorkspace(path)``, is the
    code the design is grounded in, read-only. It is passed to the Design station this
    factory builds; a ``design`` you pass keeps the workspace you gave it.

    It takes one keyword for each station, never a method: to change the review, pass
    ``design=design_station(model, method=...)``.

    It is a :class:`Factory`, so everything a factory does, such as ``run_sync``,
    ``resume_sync``, ``intake`` and ``agents()``, works on it unchanged.
    """

    def __init__(
        self,
        model: Model | str,
        *,
        workspace: AbstractCapability[Any] | None = None,
        design: Station[str, Design] | None = None,
        repairs: int = 2,
        accept: Accept | None = None,
    ) -> None:
        if not isinstance(model, (Model, str)) or not model:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise FactoryDefinitionError(
                "factory 'software'",
                ["give it a model, such as SoftwareFactory('anthropic:claude-opus-5-5')"],
            )
        if design is None:
            design = design_station(model, workspace=workspace)
        super().__init__(
            "software",
            [design],
            rework=[Rework(design, limit=repairs, on_limit="escalate")],
            accept=accept,
            version="2",
        )
