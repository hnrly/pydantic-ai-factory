"""The software factory: a mission in, an approved design out. More stations follow."""

from __future__ import annotations

import warnings
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, FactoryDefinitionError, Rework, Station
from pydantic_ai_factory.factory import Accept
from pydantic_ai_factory.methodologies import ElephantGoldfish, Goldfish

from ._design import Design
from ._station import design_review, design_station


class _Unset:
    """A 0.1 keyword that was not given."""


_UNSET: Any = _Unset()


def _deprecated(what: str, instead: str) -> None:
    warnings.warn(
        f"SoftwareFactory {what} is deprecated in 0.2 and goes in 0.3; {instead}",
        DeprecationWarning,
        stacklevel=3,
    )


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
    designer may ask you rounds of questions with :class:`~pydantic_ai_factory.AskPerson`.

    It takes one keyword for each station, never a method: to change the review, pass
    ``design=design_station(model, method=...)``. The 0.1 keywords ``rounds``,
    ``goldfish`` and ``review_model`` still work, mapped onto :func:`design_review`, and
    warn; they go in 0.3. So do the attributes that copied its settings.

    It is a :class:`Factory`, so everything a factory does, such as ``run_sync``,
    ``resume_sync``, ``intake`` and ``agents()``, works on it unchanged.
    """

    _model: Model | str
    _repairs: int

    def __init__(
        self,
        model: Model | str,
        *,
        design: Station[str, Design] | None = None,
        repairs: int = 2,
        accept: Accept | None = None,
        rounds: int = _UNSET,
        goldfish: Sequence[Goldfish] = _UNSET,
        review_model: Model | str | None = _UNSET,
    ) -> None:
        if not isinstance(model, (Model, str)) or not model:  # pyright: ignore[reportUnnecessaryIsInstance]
            raise FactoryDefinitionError(
                "factory 'software'",
                ["give it a model, such as SoftwareFactory('anthropic:claude-opus-5-5')"],
            )
        given = [
            name
            for name, value in (
                ("rounds", rounds),
                ("goldfish", goldfish),
                ("review_model", review_model),
            )
            if value is not _UNSET
        ]
        if given and design is not None:
            raise FactoryDefinitionError(
                "factory 'software'",
                [
                    f"{', '.join(given)} cannot be given with design=; set them on the "
                    "station's method, such as design_station(model, method=design_review())"
                ],
            )
        if given:
            _deprecated(
                f"keyword {', '.join(given)}",
                "pass design=design_station(model, method=...) instead",
            )
            design = design_station(model, method=_old_review(rounds, goldfish, review_model))
        elif design is None:
            design = design_station(model)
        object.__setattr__(self, "_model", model)
        object.__setattr__(self, "_repairs", repairs)
        super().__init__(
            "software",
            [design],
            rework=[Rework(design, limit=repairs, on_limit="escalate")],
            accept=accept,
            version="2",
        )

    def _review(self) -> ElephantGoldfish | None:
        found = [m for m in self.stations[0].methodologies if isinstance(m, ElephantGoldfish)]
        return found[0] if found else None

    @property
    def model(self) -> Model | str:
        """Deprecated: the designer's model, as given."""
        _deprecated("attribute model", "keep the model you passed")
        return self._model

    @property
    def repairs(self) -> int:
        """Deprecated: how many times a design that fails its gate goes back."""
        _deprecated("attribute repairs", "read the limit of software.rework[0]")
        return self._repairs

    @property
    def rounds(self) -> int | None:
        """Deprecated: the review's rounds, or ``None`` with no Elephant Goldfish."""
        _deprecated("attribute rounds", "read it from the Design station's method")
        review = self._review()
        return None if review is None else review.rounds

    @property
    def goldfish(self) -> tuple[Goldfish, ...]:
        """Deprecated: the review's goldfish, or none with no Elephant Goldfish."""
        _deprecated("attribute goldfish", "read it from the Design station's method")
        review = self._review()
        return () if review is None else tuple(review.goldfish)

    @property
    def review_model(self) -> Model | str | None:
        """Deprecated: the goldfish's model, or ``None`` when they use the designer's."""
        _deprecated("attribute review_model", "read it from the Design station's method")
        review = self._review()
        return None if review is None else review.model


def _old_review(rounds: Any, goldfish: Any, review_model: Any) -> ElephantGoldfish:
    """:func:`design_review`, with the 0.1 keywords that were given in place of its own."""
    review = design_review(None if review_model is _UNSET else review_model)
    changes: dict[str, Any] = {}
    if goldfish is not _UNSET:
        changes["goldfish"] = goldfish
    if rounds is not _UNSET:
        changes["rounds"] = rounds
        # Two clean rounds cannot be asked of fewer rounds than that.
        if isinstance(rounds, int) and not isinstance(rounds, bool) and rounds >= 1:
            changes["clean_rounds"] = min(review.clean_rounds, rounds)
    return replace(review, **changes) if changes else review
