"""The Design station's parts: the design, how it reads as Markdown, and its gate.

The station itself, its goldfish and the designer's instructions are in ``_station``.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator, Sequence
from typing import Annotated, Any, Literal, cast

from pydantic import BeforeValidator, Field, model_validator

from pydantic_ai_factory import Artifact, Evidence, Verdict, failed, gate, passed
from pydantic_ai_factory.contracts import Record

Source = Literal["person", "code", "request", "default"]
"""Where a decision came from: a person, the code, the request, or the designer's default."""

Action = Literal["create", "modify", "delete"]
"""What a change does to its file."""

CheckKind = Literal["build", "unit", "lint", "e2e", "manual"]
"""What kind of check a verification item is."""


def _ac_number(value: object) -> object:
    """Read an acceptance criterion's number, written as ``3`` or as ``"AC3"``."""
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(?:AC\s*)?(\d+)\s*", value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return value


ACNumber = Annotated[int, BeforeValidator(_ac_number)]
"""The number of an acceptance criterion: 1 is AC1."""


class Journey(Record):
    """One critical journey a person takes through the software."""

    title: str = Field(description="A short name for the journey.")
    given: str = Field(description="Where the person starts.")
    when: str = Field(description="What they do.")
    then: str = Field(description="What they see happen.")


class Decision(Record):
    """One choice the plan rests on, why it was made and where it came from."""

    topic: str = Field(description="What was decided, in a few words.")
    choice: str = Field(description="What was chosen.")
    reason: str = Field(default="", description="Why it was chosen.")
    source: Source | None = Field(
        default=None,
        description=(
            "Where the choice came from: person (a person said so), code (the existing "
            "code settles it), request (the mission says so) or default (your own default)."
        ),
    )


class Alternative(Record):
    """One way of doing the work that was considered and rejected."""

    title: str = Field(description="A short name for the alternative.")
    pros: str = Field(default="", description="What it would have given.")
    cons: str = Field(default="", description="What it would have cost.")
    rejected_because: str = Field(default="", description="Why it was rejected.")
    rejected_by: Literal["person", "drafting"] = Field(
        default="drafting",
        description="person when a person rejected it, drafting when you did while drafting.",
    )


class Risk(Record):
    """Something that could go wrong, and what the plan does about it."""

    risk: str = Field(description="What could go wrong.")
    mitigation: str = Field(default="", description="What the plan does about it.")


class Change(Record):
    """One file the work creates, modifies or deletes."""

    path: str = Field(min_length=1, description="The file's path, from the repository's root.")
    action: Action = Field(description="create, modify or delete.")
    what: str = Field(default="", description="What changes in the file.")
    why: str = Field(default="", description="Why it changes.")


class Step(Record):
    """One step of the work. Each step leaves the build passing."""

    title: str = Field(min_length=1, description="A short name for the step, unique in the design.")
    changes: list[str] = Field(
        default_factory=lambda: [],
        description="The path of every change this step makes; each is listed in changes.",
    )
    depends_on: list[str] = Field(
        default_factory=lambda: [], description="The titles of the steps that must come first."
    )
    done_when: str = Field(default="", description="How anyone can tell the step is done.")


class Check(Record):
    """One check in the verification plan, and the acceptance criteria it proves."""

    kind: CheckKind = Field(description="build, unit, lint, e2e or manual.")
    check: str = Field(description="What is run or done.")
    proves: list[ACNumber] = Field(
        default_factory=lambda: [],
        description="The numbers of the acceptance criteria this check proves: 1 is AC1.",
    )


def _empty_list() -> list[Any]:
    return []


class Design(Record):
    """A software design a person can approve and a builder can follow.

    It takes the shape of a design document: what will be built and why, how the finished
    build will be judged, and the plan a builder follows. It holds no code. Acceptance
    criteria are numbered by their place: the first is AC1. Every field but ``title``,
    ``behaviour`` and ``acceptance_criteria`` may be empty, so an older design still loads.
    """

    title: str = Field(min_length=1, max_length=120, description="A short name for the software.")
    summary: str = Field(
        default="",
        description="What will be built, why, and what happens next, in a short paragraph.",
    )
    problem: str = Field(
        default="", description="The pain this solves, and how it is handled today."
    )
    goals: list[str] = Field(
        default_factory=_empty_list, description="What the work must achieve, one sentence each."
    )
    non_goals: list[str] = Field(
        default_factory=_empty_list,
        description="What is deliberately left out, one sentence each.",
    )
    open_questions: list[str] = Field(
        default_factory=_empty_list,
        description="Anything undecided that a person must settle.",
    )
    assumptions: list[str] = Field(
        default_factory=_empty_list,
        description=(
            "Defaults nobody was asked about, where the design would still be right if the "
            "choice were different."
        ),
    )
    journeys: list[Journey] = Field(
        default_factory=_empty_list, description="The critical journeys a person takes."
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
            "numbered AC1, AC2 and so on, in this order. Do not number them yourself."
        ),
    )
    architecture: str = Field(
        default="",
        description=(
            "The components, how data flows between them, the stack, hosting and data model."
        ),
    )
    decisions: list[Decision] = Field(
        default_factory=_empty_list, description="Each choice the plan rests on."
    )
    alternatives: list[Alternative] = Field(
        default_factory=_empty_list,
        description="At least one way of doing the work that was rejected, and why.",
    )
    risks: list[Risk] = Field(default_factory=_empty_list, description="What could go wrong.")
    changes: list[Change] = Field(
        default_factory=_empty_list,
        description="Every file the work creates, modifies or deletes, each path once.",
    )
    steps: list[Step] = Field(
        default_factory=_empty_list,
        description="The work in order; together the steps make every change.",
    )
    verification: list[Check] = Field(
        default_factory=_empty_list,
        description="The checks that prove the acceptance criteria; each criterion is proved.",
    )

    @model_validator(mode="before")
    @classmethod
    def _move_old_fields(cls, data: Any) -> Any:
        """Load a stored design: ``goal`` is now ``summary``, ``exclusions`` is ``non_goals``."""
        if not isinstance(data, dict):
            return data
        moved: dict[str, Any] = dict(cast("dict[str, Any]", data))
        if not {"goal", "exclusions"} & moved.keys():
            return moved
        if "goal" in moved:
            goal = moved.pop("goal")
            if not moved.get("summary"):
                moved["summary"] = goal
        if "exclusions" in moved:
            exclusions = moved.pop("exclusions")
            if not moved.get("non_goals"):
                moved["non_goals"] = exclusions
        return moved


_LABEL = re.compile(r"^\s*AC\s*\d+\s*[:.)\-]\s*", re.IGNORECASE)
"""A label such as "AC1:" that a designer wrote at the start of a criterion."""

_NONE = "None."


def _bullets(items: Sequence[str]) -> list[str]:
    return [f"- {item}" for item in items] or [_NONE]


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _stop(text: str) -> str:
    """The text without a closing full stop, so one can be added once."""
    return text.rstrip().rstrip(".")


def _codes(paths: Sequence[str]) -> str:
    return ", ".join(f"`{path}`" for path in paths) or "none"


def render_markdown(design: Design) -> str:
    """Turn a design into Markdown. The same design always gives the same text.

    The sections come in a fixed order under fixed headings, and each section with
    nothing in it says "None.", so a person or a builder always finds each part in the
    same place. Each acceptance criterion is labelled by its place. A label the designer
    wrote itself, such as "AC1:", is dropped, so it is not printed twice.
    """
    lines = [f"# {design.title}", "", design.summary or _NONE, ""]

    def section(heading: str, body: list[str]) -> None:
        lines.extend([heading, *body, ""])

    section("## The Problem", [design.problem or _NONE])
    section("## Goals", _bullets(design.goals))
    section("## Non-Goals", _bullets(design.non_goals))
    section("## Open Questions", _bullets(design.open_questions))
    section("## Assumptions", _bullets(design.assumptions))
    journeys = [
        line
        for journey in design.journeys
        for line in (
            f"### {journey.title}",
            f"- **Given** {journey.given}",
            f"- **When** {journey.when}",
            f"- **Then** {journey.then}",
        )
    ]
    section("## Critical User Journeys", journeys or [_NONE])
    section("## Behaviour", _bullets(design.behaviour))
    section(
        "## Acceptance Criteria",
        [
            f"- **AC{n}** {_LABEL.sub('', text, count=1)}"
            for n, text in enumerate(design.acceptance_criteria, start=1)
        ],
    )
    section("## Technical Plan", [design.architecture or _NONE])
    table = [
        "| Topic | Decision | Reason | Source |",
        "|---|---|---|---|",
        *(
            f"| {_cell(d.topic)} | {_cell(d.choice)} | {_cell(d.reason)} | {d.source or ''} |"
            for d in design.decisions
        ),
    ]
    section("### Decisions this plan rests on", table if design.decisions else [_NONE])
    alternatives = [
        line
        for alternative in design.alternatives
        for line in (
            f"### {alternative.title}",
            f"- **Pros**: {alternative.pros}",
            f"- **Cons**: {alternative.cons}",
            "- **Reason for rejection**: "
            + (
                "Rejected by author: "
                if alternative.rejected_by == "person"
                else "Rejected in drafting: "
            )
            + alternative.rejected_because,
        )
    ]
    section("## Alternatives Considered", alternatives or [_NONE])
    section(
        "## Risks",
        [f"- {risk.risk} **Mitigation**: {risk.mitigation}" for risk in design.risks] or [_NONE],
    )
    lines.extend(["## Detailed Implementation Plan"])
    section(
        "### Changes",
        [
            f"- {change.action} `{change.path}`: {change.what} **Why**: {change.why}"
            for change in design.changes
        ]
        or [_NONE],
    )
    section(
        "### Order of Work",
        [
            f"{n}. **{_stop(step.title)}**. Changes: {_codes(step.changes)}. "
            f"Depends on: {', '.join(step.depends_on) or 'nothing'}. "
            f"Done when: {step.done_when}"
            for n, step in enumerate(design.steps, start=1)
        ]
        or [_NONE],
    )
    section(
        "## Verification Plan",
        [
            f"- {check.kind}: {_stop(check.check)}. Proves: "
            + (", ".join(f"AC{n}" for n in check.proves) or "nothing")
            for check in design.verification
        ]
        or [_NONE],
    )
    return "\n".join(lines)


def _open_questions(design: Design) -> list[str]:
    return [f"open question: {question}" for question in design.open_questions]


@gate(version="1")
def no_open_questions(output: Artifact[Design], evidence: Evidence) -> Verdict:
    """A design a builder can follow leaves no question open."""
    questions = _open_questions(output.payload)
    if questions:
        return failed(*questions)
    return passed("the design leaves no question open")


_PLACEHOLDER = re.compile(r"\b(TBD|TODO)\b", re.IGNORECASE)
"""A word that marks something left to do: "TBD" or "TODO", in any case."""


def _unproved_criteria(design: Design) -> Iterator[str]:
    count = len(design.acceptance_criteria)
    proved = {n for check in design.verification for n in check.proves}
    for n in range(1, count + 1):
        if n not in proved:
            yield f"AC{n} is proved by no verification item"
    for check in design.verification:
        for n in check.proves:
            if not 1 <= n <= count:
                yield f"verification '{check.check}' proves AC{n}, which does not exist"


def _unplanned_paths(design: Design) -> Iterator[str]:
    changed = {change.path for change in design.changes}
    stepped = {path for step in design.steps for path in step.changes}
    for step in design.steps:
        for path in step.changes:
            if path not in changed:
                yield f"step '{step.title}' changes `{path}`, which is not in changes"
    for change in design.changes:
        if change.path not in stepped:
            yield f"change `{change.path}` is in no step"


def _bad_order(design: Design) -> Iterator[str]:
    after = {step.title: step.depends_on for step in design.steps}
    for step in design.steps:
        for title in step.depends_on:
            if title not in after:
                yield f"step '{step.title}' depends on '{title}', which is not a step"
    done: set[str] = set()
    for start in after:
        path: list[str] = []
        loop = _find_loop(start, after, path, done)
        if loop:
            yield "the steps depend on each other in a loop: " + ", ".join(loop)
            return


def _find_loop(
    title: str, after: dict[str, list[str]], path: list[str], done: set[str]
) -> list[str] | None:
    """The first loop reached from ``title``, as the titles that make it, or None."""
    if title in path:
        return [*path[path.index(title) :], title]
    if title in done or title not in after:
        return None
    path.append(title)
    for before in after[title]:
        loop = _find_loop(before, after, path, done)
        if loop:
            return loop
    path.pop()
    done.add(title)
    return None


def _thin_decisions(design: Design) -> Iterator[str]:
    for decision in design.decisions:
        if not decision.reason.strip():
            yield f"decision '{decision.topic}' has no reason"
        if decision.source is None:
            yield f"decision '{decision.topic}' has no source"


def _unrejected(design: Design) -> Iterator[str]:
    if not design.alternatives:
        yield "the design records no alternative it rejected"
    for alternative in design.alternatives:
        if not alternative.rejected_because.strip():
            yield f"alternative '{alternative.title}' has no reason for rejection"


def _strings(value: object, where: str) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in cast("dict[str, object]", value).items():
            yield from _strings(item, f"{where}.{key}")
    elif isinstance(value, list):
        for n, item in enumerate(cast("list[object]", value)):
            yield from _strings(item, f"{where}[{n}]")


def _placeholders(design: Design) -> Iterator[str]:
    for name, value in design.model_dump().items():
        for where, text in _strings(value, name):
            match = _PLACEHOLDER.search(text)
            if match:
                yield f"`{where}` holds {match.group(1)}"


def _repeated_paths(design: Design) -> Iterator[str]:
    counts = Counter(change.path for change in design.changes)
    for path, count in counts.items():
        if count > 1:
            yield f"`{path}` appears {count} times in changes"


_CHECKS = (
    _unproved_criteria,
    _unplanned_paths,
    _bad_order,
    _thin_decisions,
    _unrejected,
    _placeholders,
    _repeated_paths,
)
"""The seven checks of :func:`design_is_buildable`, in the order they run."""


@gate(version="1")
def design_is_buildable(output: Artifact[Design], evidence: Evidence) -> Verdict:
    """A builder can follow the design: nothing open, nothing missing, nothing left to do.

    It runs the rule of :func:`no_open_questions` and then seven checks: every acceptance
    criterion is proved and every criterion named exists; every path in a step is a
    change and every change is in a step; every step a step depends on exists, with no
    loop; every decision has a reason and a source; at least one alternative, each with a
    reason for rejection; no field says "TBD" or "TODO"; and no path is changed twice.
    Each failure names the item. Plain code decides; no model does.
    """
    design = output.payload
    problems = _open_questions(design)
    for check in _CHECKS:
        problems.extend(check(design))
    if problems:
        return failed(*problems)
    return passed("a builder can follow the design")
