"""The Design station's parts: the design, its goldfish and standards, and its gate."""

from __future__ import annotations

from pydantic import Field

from pydantic_ai_factory import Artifact, Evidence, Verdict, failed, gate, passed
from pydantic_ai_factory.contracts import Record
from pydantic_ai_factory.methodologies import Goldfish, Standard


class Design(Record):
    """A software design a person can approve and a builder can follow.

    It says what will be built and how the finished build will be judged. It holds no
    code. Acceptance criteria are numbered by their place: the first is AC1.
    """

    title: str = Field(min_length=1, max_length=120, description="A short name for the software.")
    goal: str = Field(
        min_length=1,
        description="The mission, restated: what the software is for and who it is for.",
    )
    behaviour: list[str] = Field(
        min_length=1,
        description=(
            "What the software does, one statement each, including what happens on blank "
            "or unusual input."
        ),
    )
    acceptance_criteria: list[str] = Field(
        min_length=1,
        description=(
            "How a finished build will be judged, one checkable sentence each. They are "
            "numbered AC1, AC2 and so on, in this order."
        ),
    )
    exclusions: list[str] = Field(
        default_factory=lambda: [], description="What is deliberately left out."
    )
    open_questions: list[str] = Field(
        default_factory=lambda: [],
        description="Anything the mission left undecided that a person must settle.",
    )


def render_markdown(design: Design) -> str:
    """Turn a design into Markdown. The same design always gives the same text."""
    sections = (
        ("Behaviour", design.behaviour),
        (
            "Acceptance criteria",
            [f"**AC{n}** {text}" for n, text in enumerate(design.acceptance_criteria, start=1)],
        ),
        ("Exclusions", design.exclusions),
        ("Open questions", design.open_questions),
    )
    lines = [f"# {design.title}", "", design.goal, ""]
    for heading, items in sections:
        lines += [f"## {heading}", ""]
        lines += [f"- {item}" for item in items] or ["None."]
        lines.append("")
    return "\n".join(lines)


CLEAR = Standard(
    "clear",
    "Someone who has never seen the mission can explain the design back without guessing. "
    "Every term is explained before it is used, and no passage has two readings.",
)
FAITHFUL = Standard(
    "faithful",
    "The design covers everything its goal needs and nothing it does not. It says what "
    "happens on blank or unusual input, and every acceptance criterion can be checked by "
    "using or reading the finished software.",
)
BUILDABLE = Standard(
    "buildable",
    "A builder could start from the design alone, without asking anything, and would know "
    "when they had finished.",
)

DESIGN_STANDARDS: tuple[Standard, ...] = (CLEAR, FAITHFUL, BUILDABLE)
"""What the design goldfish hold a design to: clear, faithful to its goal, and buildable."""

DESIGN_GOLDFISH: tuple[Goldfish, ...] = (
    Goldfish(
        "comprehension",
        stage="comprehension",
        job="Explain the design back, as if to a builder who has never seen the mission.",
        standard=CLEAR,
    ),
    Goldfish(
        "critique",
        stage="critique",
        job=(
            "Check the design against its goal, which restates the mission. Report what "
            "the goal needs and the design misses, what the design adds that the goal does "
            "not need, and any acceptance criterion that cannot be checked."
        ),
        standard=FAITHFUL,
    ),
    Goldfish(
        "readiness",
        stage="readiness",
        job="Say whether a builder could start from this design without asking anything.",
        standard=BUILDABLE,
    ),
)
"""Three goldfish for a software design: can it be explained back, is it right, can it be built."""

DESIGNER_INSTRUCTIONS = (
    "You write software designs. Work only from the mission you are given. Say what will be "
    "built and how a finished build will be judged; do not write code. Restate the mission "
    "as the goal. Keep every requirement and constraint in the mission and add none of your "
    "own. Say what happens on blank or unusual input. Make every acceptance criterion one "
    "checkable sentence. If the mission leaves something undecided that matters, ask the "
    "person before you draft; list anything still undecided as an open question instead of "
    "guessing. When a design comes back with open questions or a person's notes, settle "
    "each one and revise. Never say the design is approved or built."
)


@gate(version="1")
def no_open_questions(output: Artifact[Design], evidence: Evidence) -> Verdict:
    """A design a builder can follow leaves no question open."""
    questions = output.payload.open_questions
    if questions:
        return failed(*(f"open question: {question}" for question in questions))
    return passed("the design leaves no question open")
