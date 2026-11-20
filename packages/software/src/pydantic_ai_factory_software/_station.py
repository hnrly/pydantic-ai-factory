"""The Design station, composed as a person would compose their own.

A designer agent drafts a :class:`Design` under a method, with its skills beside it.
``design_review`` is Elephant Goldfish tuned for software designs, and ``design_station``
is the station a :class:`SoftwareFactory` runs, or any factory of your own.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai.workspaces import LocalWorkspaceBackend, ReadOnlyWorkspace, Workspace
from pydantic_ai_harness.skills import Skills

from pydantic_ai_factory import AskPerson, FactoryDefinitionError, Station, station
from pydantic_ai_factory.methodologies import ElephantGoldfish, Goldfish, Standard

from ._design import Design, design_is_buildable, render_markdown

SKILLS = Path(__file__).parent / "skills"
"""The software package's skills: design-read-code, design-doc and goldfish-readiness."""

DESIGNER_SKILLS = ("design-read-code", "design-doc")
"""The software package's skills that the designer is offered. Readiness is a goldfish's."""

# Ported from Google's Elephant Goldfish plugin, its implementation readiness and codebase
# details agents and its implementation readiness standard, and from coship-dev's
# `check-readiness` helper. Its text is the body of the skill goldfish-readiness.
READINESS = Goldfish.from_skill(SKILLS / "goldfish-readiness", sees_request=True)
"""Reads the design as the engineer who will build it, and checks it against the code."""

# Comprehension is ported from Google's comprehension agent and coship-dev's
# `check-comprehension`; critique from Google's design critic and feature specification
# standard and coship-dev's `critique-design`. Both are Elephant Goldfish's own skills.
DESIGN_GOLDFISH: tuple[Goldfish, ...] = (*ElephantGoldfish.GOLDFISH, READINESS)
"""Three goldfish for a software design: can it be explained back, is it right, can it be built."""

# Google's clarity, feature specification and implementation readiness standards, as each
# goldfish's skill states them.
DESIGN_STANDARDS: tuple[Standard, ...] = tuple(
    fish.standard for fish in DESIGN_GOLDFISH if fish.standard is not None
)
"""What the design goldfish hold a design to, read from their skills: clear, faithful, buildable."""

# Ported from Google's `egm-draft` and coship-dev's `design` skill, in this factory's words.
_INSTRUCTIONS = """\
You write software designs that a person can approve and a builder can follow
without asking anything. You do not write code.

Your method is in skills. Load design-read-code before you ask anything, and
design-doc before you draft.

Before you ask anything, read what you are given. Take every fact you can from the
mission, and from any code and instruction files you can read. Fit the design to the
code that exists, and reuse what is there.

Ask the person only what a person must decide: who the work is for and what done
looks like, what is out of scope, hard limits such as stack, hosting, data and
volumes, and any choice with more than one defensible answer. Ask in rounds of up
to four questions with ask_person. For each choice, give up to three options,
with what each costs and when it fails, and put the option you recommend first.
Never ask what the code or the mission already settles.

Keep every requirement and constraint in the mission, and add none of your own.
Say what happens on blank or unusual input. Make each acceptance criterion one
checkable sentence.

Record each decision with its reason and where it came from: a person, the code,
the request, or your own default. Record at least one alternative you rejected,
and why. List every file you will create, modify or delete, with what changes and
why. Order the work in steps that each leave the build passing, and say when each
step is done. Name the check that proves each acceptance criterion.

If you choose a default that nobody was asked about, and the design would still
be right if the choice were different, record it under assumptions. If the design
depends on the choice, it is an open question: ask, or list it.

A finding you escalate goes to a person; do not also list it as an open
question. When a design comes back with open questions or a person's notes,
settle each one and revise. Never say the design is approved or built.
"""

DESIGNER_INSTRUCTIONS = "\n\n".join(" ".join(part.split()) for part in _INSTRUCTIONS.split("\n\n"))
"""What the designer is told: its skills, what to ask, what to record, and what it never does.

Each paragraph is one line, so no sentence is broken in two.
"""


class _Default:
    """The method ``design_station`` uses when it is given none: ``design_review()``."""

    def __repr__(self) -> str:
        return "DEFAULT"


DEFAULT: Any = _Default()


def design_review(model: Model | str | None = None) -> ElephantGoldfish:
    """Elephant Goldfish tuned for software designs.

    The three goldfish of :data:`DESIGN_GOLDFISH` read each draft as Markdown, for up to
    four rounds, until two clean rounds run in a row. A contest that cites a field of the
    design, or a file and symbol, settles its finding when code checks the citation.
    ``model`` runs the goldfish; by default they use the designer's.
    """
    return ElephantGoldfish(
        goldfish=DESIGN_GOLDFISH,
        rounds=4,
        clean_rounds=2,
        settle_contests=True,
        to_text=render_markdown,
        model=model,
    )


def designer_skills() -> Skills[Any]:
    """The designer's catalogue of the software package's skills.

    It joins the method's catalogue on the same agent. With ``keep_first``, a skill here
    with a method skill's name never replaces it; the method is attached first.
    """
    folder = ReadOnlyWorkspace(Workspace(LocalWorkspaceBackend(SKILLS)))
    return Skills(".", include=DESIGNER_SKILLS, duplicate_names="keep_first", workspace=folder)


def design_station(
    model: Model | str, *, method: AbstractCapability[Any] | None = DEFAULT
) -> Station[str, Design]:
    """The Design station: a designer that drafts a :class:`Design` a person approves.

    The designer may ask a person rounds of questions with
    :class:`~pydantic_ai_factory.AskPerson`, and is offered the software package's skills,
    design-read-code and design-doc, beside any its method offers. ``method`` reviews each
    draft; by default it is :func:`design_review`, and ``None`` gives a designer with no
    method at all. A design that fails :func:`design_is_buildable` goes back to the
    designer; one that passes waits for a person to approve it.
    """
    if not isinstance(model, (Model, str)) or not model:  # pyright: ignore[reportUnnecessaryIsInstance]
        raise FactoryDefinitionError(
            "station 'design'",
            ["give it a model, such as design_station('anthropic:claude-opus-5-5')"],
        )
    if method is DEFAULT:
        method = design_review()
    if method is not None and not isinstance(method, AbstractCapability):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise FactoryDefinitionError(
            "station 'design'", [f"{method!r} is not a method; pass a capability or None"]
        )
    designer = Agent(
        model,
        name="designer",
        output_type=Design,
        instructions=DESIGNER_INSTRUCTIONS,
        capabilities=[
            # The method comes before the designer's skills, so its own skills come first
            # in the one catalogue the designer sees.
            *([method] if method is not None else []),
            AskPerson(),
            designer_skills(),
        ],
        # Each review round resends the whole conversation, so cache it.
        model_settings={"cache": True},
    )
    return station(designer, name="design", version="2", gate=design_is_buildable, approval=True)
