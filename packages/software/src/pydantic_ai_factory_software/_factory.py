"""The software factory: a mission in, an approved design out. More stations follow."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, FactoryDefinitionError, Rework, station
from pydantic_ai_factory.factory import Accept
from pydantic_ai_factory.methodologies import ElephantGoldfish, Goldfish

from ._design import (
    DESIGN_GOLDFISH,
    DESIGNER_INSTRUCTIONS,
    Design,
    no_open_questions,
    render_markdown,
)


@dataclass(frozen=True, init=False, eq=False, kw_only=True)
class SoftwareFactory(Factory):
    """A ready-made factory for building software. Today it has one station, Design.

    Run it with a mission, or a string that becomes one. In the Design station a designer
    agent drafts a :class:`Design` under the Elephant Goldfish method: ``goldfish`` read
    each draft, for up to ``rounds`` rounds, and the designer revises in the same
    conversation. A design that still has open questions is sent back to the designer
    up to ``repairs`` times; after that a person decides. A design that passes waits for
    a person to approve it.

    ``model`` drafts the design. ``review_model`` runs the goldfish; by default they use
    ``model``. ``accept`` reads signals, as for any :class:`Factory`.

    It is a :class:`Factory`, so everything a factory does, such as ``run_sync``,
    ``resume_sync``, ``intake`` and ``agents()``, works on it unchanged.
    """

    model: Model | str
    rounds: int
    repairs: int
    goldfish: tuple[Goldfish, ...]
    review_model: Model | str | None

    def __init__(
        self,
        model: Model | str,
        *,
        rounds: int = 2,
        repairs: int = 2,
        goldfish: Sequence[Goldfish] = DESIGN_GOLDFISH,
        review_model: Model | str | None = None,
        accept: Accept | None = None,
    ) -> None:
        if not isinstance(model, (Model, str)) or not model:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise FactoryDefinitionError(
                "factory 'software'",
                ["give it a model, such as SoftwareFactory('anthropic:claude-opus-5-5')"],
            )
        review = ElephantGoldfish(goldfish=goldfish, rounds=rounds, model=review_model)
        designer = Agent(
            model,
            name="designer",
            output_type=Design,
            instructions=DESIGNER_INSTRUCTIONS,
            capabilities=[review],
            # Each review round resends the whole conversation, so cache it.
            model_settings={"cache": True},
        )
        design = station(
            designer,
            name="design",
            version="1",
            gate=no_open_questions,
            approval=True,
            render=render_markdown,
        )
        for label, value in (
            ("model", model),
            ("rounds", rounds),
            ("repairs", repairs),
            ("goldfish", tuple(review.goldfish)),
            ("review_model", review_model),
        ):
            object.__setattr__(self, label, value)
        super().__init__(
            "software",
            [design],
            rework=[Rework(design, limit=repairs, on_limit="escalate")],
            accept=accept,
            version="1",
        )
