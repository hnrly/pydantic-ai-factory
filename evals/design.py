"""Does independent review make designs better? Score the Design station both ways.

The same missions are run twice through a one-station factory built like the software
factory's Design station: once with a designer that has the Elephant Goldfish capability,
and once with the same agent without it. Every score comes from plain code reading the
design, not from a model's opinion.

    python -m evals.design --offline
    python -m evals.design --model anthropic:claude-opus-5-5 --out results.json

``--offline`` uses a scripted model. It proves the evaluation runs, and shows the shape
of the answer. Only a real model tells you anything about real designs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryFeedbackPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from pydantic_ai_factory import Action, Factory, MemoryStore, Question, Rework, Run, station
from pydantic_ai_factory.methodologies import ElephantGoldfish, GoldfishReport
from pydantic_ai_factory_software import DESIGN_GOLDFISH, Design
from pydantic_ai_factory_software._design import DESIGNER_INSTRUCTIONS, no_open_questions

DECIDER = "eval"
"""Who the evaluation says approved each design. Nobody read it."""

ANSWER = "Decide for yourself, and say what you decided in the design."
"""The answer to any question a designer asks a person during the evaluation."""

MAX_ANSWERS = 10
"""How many times one run may wait before the evaluation gives up on it."""


class HiddenCheck(BaseModel):
    """Something the mission leaves unsaid that a good design must still settle.

    The design settles it when one of its statements matches every pattern and none of
    the ``unless`` patterns. A statement is one behaviour, acceptance criterion or
    exclusion. Patterns are regular expressions, matched without regard to case.
    """

    name: str
    question: str
    """What the design must decide, in plain words."""
    patterns: tuple[str, ...]
    unless: tuple[str, ...] = ()
    """Patterns that mean the statement dodges the question, such as "no deleting"."""

    def settled_by(self, statement: str) -> bool:
        found = all(re.search(pattern, statement, re.IGNORECASE) for pattern in self.patterns)
        return found and not any(re.search(p, statement, re.IGNORECASE) for p in self.unless)


class Expectation(BaseModel):
    """What a faithful design of one mission must contain."""

    must_cover: tuple[str, ...]
    """Words the mission turns on. A design that drops one has lost part of the mission."""

    hidden: tuple[HiddenCheck, ...] = ()
    """Things the mission leaves unsaid that a good design must settle. The designer never
    sees them. They measure judgement, where ``must_cover`` measures faithfulness."""

    @property
    def min_criteria(self) -> int:
        """How many acceptance criteria a build of this mission needs: one per thing to check."""
        return len(self.must_cover)


class DesignResult(BaseModel):
    """What one run produced, and what it took to get there."""

    design: Design | None
    outcome: str
    attempts: int
    rounds: int
    open_findings: int

    @property
    def text(self) -> str:
        return self.design.model_dump_json().lower() if self.design else ""


TODO = "A to-do list web page where I can add tasks and tick them off."

_REFUSED = r"\b(nothing|ignored?|not (be )?added|no task|reject\w*|refus\w*|unchanged|disabled)\b"
_NO_DELETING = (r"\bno (deleting|deletion|delete)\b|\bcannot be deleted\b|\bnot deletable\b",)
BLANK_TASK = HiddenCheck(
    name="blank-task",
    question="What happens when someone adds a blank task?",
    patterns=(r"\b(blank|empty)\b", _REFUSED),
)
WHITESPACE_TASK = HiddenCheck(
    name="whitespace-task",
    question="Does a task of only spaces count as blank?",
    patterns=(r"\b(whitespace|spaces)\b", rf"\bblank\b|{_REFUSED}"),
)
SURVIVES_RELOAD = HiddenCheck(
    name="survives-reload",
    question="Do tasks survive a reload of the page?",
    patterns=(r"\b(reload\w*|refresh\w*|persist\w*|reopen\w*)\b",),
)
UNTICK = HiddenCheck(
    name="untick",
    question="Can a ticked task be unticked?",
    patterns=(r"\b(untick\w*|un-tick\w*|uncheck\w*|tick\w* (it |a task )?again)\b",),
)
STORAGE_FAILS = HiddenCheck(
    name="storage-fails",
    question="What happens when saved tasks cannot be read or written?",
    patterns=(
        r"\b(unavailable|corrupt\w*|unreadable|private browsing|quota|full|blocked|disabled)\b",
        r"\b(storage|localstorage|saved?|saving)\b",
    ),
)
ORDER_AFTER_RELOAD = HiddenCheck(
    name="order-after-reload",
    question="Do tasks keep their order after a reload?",
    patterns=(r"\border\b", r"\b(reload\w*|refresh\w*)\b"),
)
DELETE_TICKED = HiddenCheck(
    name="delete-ticked",
    question="Can a ticked task be deleted too?",
    patterns=(r"\bdelet\w*", r"\b(ticked|done|completed|any task|every task|each task)\b"),
    unless=_NO_DELETING,
)
DELETE_CONFIRM = HiddenCheck(
    name="delete-confirm",
    question="Is deleting confirmed first, or undoable?",
    patterns=(
        r"\bdelet\w*",
        r"\b(confirm\w*|undo\w*|immediately|without asking|cannot be undone|permanent\w*)\b",
    ),
    unless=_NO_DELETING,
)
NO_DUE_DATE = HiddenCheck(
    name="no-due-date",
    question="Can a task be added without a due date, and where does it go?",
    patterns=(
        r"\btasks?\b",
        r"(\b(without|no|missing|blank|empty)\b.{0,40}\bdue date|\bdue date\b.{0,40}"
        r"\b(optional|required|mandatory|must be given)\b)",
    ),
)
DUE_TODAY = HiddenCheck(
    name="due-today",
    question="Is a task due today overdue?",
    patterns=(r"\btoday\b",),
)
ORDER_OF_OVERDUE = HiddenCheck(
    name="order-of-overdue",
    question="In what order are the overdue tasks themselves shown?",
    patterns=(r"\boverdue\b", r"\b(earliest|oldest|soonest|latest|most overdue|sorted|ordered)\b"),
)

CASES: tuple[Case[str, DesignResult, Expectation], ...] = (
    Case(
        name="todo-list",
        inputs=TODO,
        metadata=Expectation(
            must_cover=("to-do", "add", "tick"),
            hidden=(BLANK_TASK, SURVIVES_RELOAD, UNTICK),
        ),
    ),
    Case(
        name="keep-ticked-tasks",
        inputs=f"{TODO} Ticked tasks stay ticked after a reload.",
        metadata=Expectation(
            must_cover=("add", "tick", "reload"),
            hidden=(BLANK_TASK, STORAGE_FAILS, ORDER_AFTER_RELOAD),
        ),
    ),
    Case(
        name="delete-tasks",
        inputs=f"{TODO} I can delete a task, and a blank task is never added.",
        metadata=Expectation(
            must_cover=("add", "tick", "delete", "blank"),
            hidden=(WHITESPACE_TASK, DELETE_TICKED, DELETE_CONFIRM),
        ),
    ),
    Case(
        name="due-dates",
        inputs=(
            "A to-do list web page where I can add tasks with a due date and tick them off. "
            "Overdue tasks are shown first."
        ),
        metadata=Expectation(
            must_cover=("add", "due date", "tick", "overdue"),
            hidden=(NO_DUE_DATE, DUE_TODAY, ORDER_OF_OVERDUE),
        ),
    ),
)


@dataclass
class CoversTheMission(Evaluator[str, DesignResult, Expectation]):
    """The share of the mission's key words that appear somewhere in the design."""

    def evaluate(self, ctx: EvaluatorContext[str, DesignResult, Expectation]) -> float:
        expected = ctx.metadata
        if expected is None or ctx.output.design is None:
            return 0.0
        found = [term for term in expected.must_cover if term in ctx.output.text]
        return len(found) / len(expected.must_cover)


@dataclass
class EnoughCriteria(Evaluator[str, DesignResult, Expectation]):
    """Whether the design has as many acceptance criteria as the mission has things to check."""

    def evaluate(self, ctx: EvaluatorContext[str, DesignResult, Expectation]) -> float:
        expected = ctx.metadata
        if expected is None or ctx.output.design is None:
            return 0.0
        return min(1.0, len(ctx.output.design.acceptance_criteria) / expected.min_criteria)


@dataclass
class CriteriaTiedToTheMission(Evaluator[str, DesignResult, Expectation]):
    """The share of acceptance criteria that name something the mission asked for.

    A criterion such as "it works" names nothing, and cannot be checked.
    """

    def evaluate(self, ctx: EvaluatorContext[str, DesignResult, Expectation]) -> float:
        expected = ctx.metadata
        if expected is None or ctx.output.design is None:
            return 0.0
        criteria = ctx.output.design.acceptance_criteria
        tied = [
            criterion
            for criterion in criteria
            if any(term in criterion.lower() for term in expected.must_cover)
        ]
        return len(tied) / len(criteria)


@dataclass
class SettlesTheUnsaid(Evaluator[str, DesignResult, Expectation]):
    """The share of the case's hidden checks that the design settles.

    Each hidden check is something the mission leaves unsaid, such as what a blank task
    does. A design settles it when one of its statements matches the check. This is where
    review should show its value: a fresh reader asks the questions the author forgot.
    """

    def evaluate(self, ctx: EvaluatorContext[str, DesignResult, Expectation]) -> float:
        expected = ctx.metadata
        if expected is None or not expected.hidden or ctx.output.design is None:
            return 0.0
        statements = statements_of(ctx.output.design)
        settled = [
            check
            for check in expected.hidden
            if any(check.settled_by(statement) for statement in statements)
        ]
        return len(settled) / len(expected.hidden)


def statements_of(design: Design) -> list[str]:
    """Every statement a design makes: its behaviour, criteria and exclusions."""
    return [*design.behaviour, *design.acceptance_criteria, *design.exclusions]


@dataclass
class Effort(Evaluator[str, DesignResult, Expectation]):
    """What the result cost: attempts, rounds of review, and blocking findings on the way."""

    def evaluate(self, ctx: EvaluatorContext[str, DesignResult, Expectation]) -> dict[str, float]:
        return {
            "attempts": float(ctx.output.attempts),
            "rounds": float(ctx.output.rounds),
            "open_findings": float(ctx.output.open_findings),
        }


DATASET: Dataset[str, DesignResult, Expectation] = Dataset(
    name="design",
    cases=list(CASES),
    evaluators=[
        CoversTheMission(),
        EnoughCriteria(),
        CriteriaTiedToTheMission(),
        SettlesTheUnsaid(),
        Effort(),
    ],
)

SCORES = ("CoversTheMission", "EnoughCriteria", "CriteriaTiedToTheMission", "SettlesTheUnsaid")
EFFORT = ("attempts", "rounds", "open_findings")


def designers(model: Model | str) -> tuple[Agent[None, Design], Agent[None, Design]]:
    """The two arms: the designer with Elephant Goldfish, and the same agent without it.

    Both are named ``designer``, so each arm's records look the same apart from the
    methodologies stamped on them.
    """
    designer = Agent(
        model,
        name="designer",
        output_type=Design,
        instructions=DESIGNER_INSTRUCTIONS,
        capabilities=[ElephantGoldfish(goldfish=DESIGN_GOLDFISH)],
        model_settings={"cache": True},
    )
    plain_designer = Agent(
        model,
        name="designer",
        output_type=Design,
        instructions=DESIGNER_INSTRUCTIONS,
        model_settings={"cache": True},
    )
    return designer, plain_designer


def design_factory(designer: Agent[None, Design]) -> Factory:
    """A one-station factory around ``designer``, shaped like the software factory's."""
    design = station(designer, name="design", version="1", gate=no_open_questions, approval=True)
    return Factory("software", [design], rework=[Rework(design, limit=2, on_limit="escalate")])


async def run_design(designer: Agent[None, Design], mission: str) -> DesignResult:
    """Run one mission and approve whatever reaches a person, without reading it.

    Approving without reading is wrong in real use. Here it is the point: the scores
    measure what the factory would have put in front of a person. A question the designer
    asks is answered with :data:`ANSWER`.
    """
    factory = design_factory(designer)
    store = MemoryStore()
    run = await factory.run(mission, store=store)
    for _ in range(MAX_ANSWERS):
        if run.waiting is None:
            break
        answer: Any
        if run.waiting.kind == "tool-calls":
            answers: dict[Question | Action, str | bool] = dict.fromkeys(run.questions, ANSWER)
            answers |= dict.fromkeys(run.actions, True)
            answer = run.reply(answers, by=DECIDER)
        else:
            answer = run.approve(by=DECIDER)
        run = await factory.resume(answer, store=store)
    design = None
    if run.outcome == "passed" and run.output_ref is not None:
        design = (await store.get(run.output_ref, Design)).payload
    reports = await _reports(run, store)
    return DesignResult(
        design=design,
        outcome=run.outcome,
        attempts=run.attempts("design"),
        rounds=len(reports),
        open_findings=sum(1 for report in reports for f in report.findings if f.blocking),
    )


async def _reports(run: Run, store: MemoryStore) -> list[GoldfishReport]:
    """Every goldfish report the run kept, once each."""
    refs = dict.fromkeys(
        ref for step in run.steps for ref in step.evidence if ref.type == "GoldfishReport"
    )
    return [(await store.get(ref, GoldfishReport)).payload for ref in refs]


async def evaluate(
    designer: Agent[None, Design], *, name: str
) -> EvaluationReport[str, DesignResult, Expectation]:
    """Score every case with one arm's designer."""

    async def task(mission: str) -> DesignResult:
        return await run_design(designer, mission)

    return await DATASET.evaluate(task, name=name, progress=False, max_concurrency=2)


def summarise(report: EvaluationReport[str, DesignResult, Expectation]) -> dict[str, float]:
    """Average each score and each measure of effort across the cases."""
    averages = report.averages()
    scores: dict[str, float | int] = dict(averages.scores) if averages else {}
    return {name: round(float(value), 3) for name, value in sorted(scores.items())}


def table(with_review: dict[str, float], without: dict[str, float]) -> str:
    """The two arms side by side, with the difference."""
    rows = [f"{'':30}{'with review':>14}{'without':>14}{'difference':>14}"]
    for name in (*SCORES, *EFFORT):
        a, b = with_review.get(name, 0.0), without.get(name, 0.0)
        rows.append(f"{name:30}{a:>14.2f}{b:>14.2f}{a - b:>+14.2f}")
    return "\n".join(rows)


async def compare(model: Model | str) -> dict[str, Any]:
    """Run both arms on the same model and summarise each."""
    designer, plain_designer = designers(model)
    reviewed = await evaluate(designer, name="with review")
    plain = await evaluate(plain_designer, name="without review")
    return {
        "cases": [case.name for case in CASES],
        "with_review": summarise(reviewed),
        "without_review": summarise(plain),
        "failures": len(reviewed.failures) + len(plain.failures),
    }


# The scripted model behind --offline. Its designer leaves acceptance criteria thin on
# its first draft and fixes them when a goldfish objects, so the offline result is true
# by construction: it proves the evaluation runs and that its scores can tell a thin
# design from a full one, and nothing more.

THIN = "It works."
THIN_ISSUE = "The acceptance criteria are too vague to check."
_CLAUSE = re.compile(r",\s*|\s+(?:and|where|with)\s+|(?<=[.!?])\s+")


def _said(messages: list[ModelMessage]) -> list[str]:
    """Every prompt, tool result and retry prompt the model was sent, in order."""
    return [
        str(part.content)
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart | ToolReturnPart | RetryFeedbackPart)
    ]


def _scripted_design(mission: str, *, revised: bool) -> dict[str, Any]:
    clauses = [c.strip(" .") for c in _CLAUSE.split(" ".join(mission.split())) if c.strip(" .")]
    criteria = [f"A person can check that: {clause}." for clause in clauses]
    return {
        "title": "To-do list",
        "goal": mission,
        "behaviour": [f"{clause}." for clause in clauses],
        "acceptance_criteria": criteria if revised else [THIN],
        "exclusions": ["Anything the mission does not ask for."],
        "open_questions": [],
    }


def _scripted(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    tool = info.output_tools[0]
    fields = set(tool.parameters_json_schema.get("properties", {}))
    said = _said(messages)
    if "findings" in fields:
        work = (said or [""])[0]
        critique = "Stage: critique" in (info.instructions or "")
        findings = [{"severity": "Major", "issue": THIN_ISSUE}] if critique and THIN in work else []
        reply: dict[str, Any] = {"findings": findings}
        if "paraphrase" in fields:
            reply["paraphrase"] = "A to-do list page, explained back in brief."
        return ModelResponse(parts=[ToolCallPart(tool.name, reply)])
    revised = any("Goldfish review" in text for text in said[1:])
    design = _scripted_design(said[0] if said else "", revised=revised)
    return ModelResponse(parts=[ToolCallPart(tool.name, design)])


def offline_model() -> FunctionModel:
    """The scripted model for ``--offline``. It makes no network calls and needs no key."""
    return FunctionModel(_scripted, model_name="offline")


def main(argv: Sequence[str] | None = None) -> int:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    parser = argparse.ArgumentParser(
        description="Score the Design station with and without Elephant Goldfish."
    )
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--offline", action="store_true", help="use a scripted model; no key")
    choice.add_argument("--model", help="a Pydantic AI model name")
    parser.add_argument("--out", type=Path, help="also write the results as JSON here")
    args = parser.parse_args(argv)

    model: Model | str = offline_model() if args.offline else str(args.model)
    results = asyncio.run(compare(model))
    results["model"] = "offline (scripted)" if args.offline else str(args.model)

    print(f"Design station, {len(CASES)} missions, model: {results['model']}")
    print()
    print(table(results["with_review"], results["without_review"]))
    if args.offline:
        print()
        print("These numbers come from a scripted model. They show the evaluation works,")
        print("not how a real model behaves. Run with --model for that.")
    if results["failures"]:
        print(f"\n{results['failures']} case(s) could not be scored.")
    if args.out:
        args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    return 1 if results["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
