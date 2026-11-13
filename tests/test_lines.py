"""A line moves work through stations, sends failures back a bounded number of times,
and never retries a station that did not run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    Line,
    LineDefinitionError,
    MemoryStore,
    MethodologyRef,
    Rework,
    StationContext,
    StationInputInvalid,
    Verdict,
    failed,
    gate,
    needs_approval,
    passed,
    run_line,
    station,
)

pytestmark = pytest.mark.anyio


class Brief(BaseModel):
    goal: str


class Draft(BaseModel):
    text: str
    revision: int = 1


class Checked(BaseModel):
    text: str
    problems: list[str] = []


class Published(BaseModel):
    url: str


@dataclass
class Deps:
    """Scripts what the test stations do, and remembers what they saw."""

    problems: list[list[str]] = field(default_factory=lambda: [])
    break_check: bool = False
    seen: list[tuple[str, int, tuple[str, ...]]] = field(default_factory=lambda: [])


@station
async def draft(ctx: StationContext[Deps], brief: Brief) -> Draft:
    reasons = ctx.feedback.reasons if ctx.feedback else ()
    ctx.deps.seen.append(("draft", ctx.attempt, reasons))
    return Draft(text=brief.goal, revision=ctx.attempt)


@gate(version="1")
def no_problems(output: Artifact[Checked], evidence: Evidence) -> Verdict:
    return failed(*output.payload.problems) if output.payload.problems else passed("clean")


@station(gate=no_problems)
async def check(ctx: StationContext[Deps], draft: Draft) -> Checked:
    ctx.deps.seen.append(("check", ctx.attempt, ()))
    if ctx.deps.break_check:
        raise ConnectionError("the checker is unreachable")
    problems = ctx.deps.problems.pop(0) if ctx.deps.problems else []
    return Checked(text=draft.text, problems=problems)


@station
async def publish(ctx: StationContext[Deps], checked: Checked) -> Published:
    ctx.deps.seen.append(("publish", ctx.attempt, ()))
    return Published(url=f"https://example.org/{len(checked.text)}")


def line(*rework: Rework) -> Line:
    return Line("document", [draft, check, publish], rework=list(rework))


def outcomes(run: Any) -> list[tuple[str, int, str]]:
    return [(step.station, step.attempt, step.outcome) for step in run.steps]


async def test_work_moves_through_every_station_in_order() -> None:
    store = MemoryStore()
    stamp = MethodologyRef(name="review", version="1")

    run = await run_line(
        line(),
        Brief(goal="Split a bill"),
        store=store,
        deps=Deps(),
        run_id="run-1",
        mission_id="m-1",
        methodologies=[stamp],
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("passed", True, None)
    assert (run.run_id, run.line, run.line_version) == ("run-1", "document", "0")
    assert (run.mission_id, run.methodologies) == ("m-1", (stamp,))
    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "passed"),
        ("publish", 1, "not_judged"),
    ]
    assert run.output is not None
    assert (await store.get(run.output, Published)).payload.url == "https://example.org/12"


async def test_each_output_is_the_next_stations_input_all_the_way_back() -> None:
    store = MemoryStore()

    run = await run_line(line(), Brief(goal="Split a bill"), store=store, deps=Deps())

    assert run.output is not None
    published = await store.get(run.output, Published)
    checked = await store.get(published.inputs[0], Checked)
    drafted = await store.get(checked.inputs[0], Draft)
    assert drafted.inputs == (run.input,)
    assert {a.produced_by.run_id for a in (published, checked, drafted) if a.produced_by} == {
        run.run_id
    }


async def test_a_failure_with_no_rework_rule_stops_the_line() -> None:
    deps = Deps(problems=[["too short"]])

    run = await run_line(line(), Brief(goal="x"), store=MemoryStore(), deps=deps)

    assert (run.outcome, run.finished, run.stopped_at, run.output) == (
        "failed",
        False,
        "check",
        None,
    )
    assert outcomes(run) == [("draft", 1, "not_judged"), ("check", 1, "failed")]


async def test_a_failure_sends_work_back_with_the_reasons_and_then_passes() -> None:
    deps = Deps(problems=[["too short", "no title"]])

    run = await run_line(
        line(Rework("check", "draft", limit=2)), Brief(goal="x"), store=MemoryStore(), deps=deps
    )

    assert (run.outcome, run.finished) == ("passed", True)
    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "failed"),
        ("draft", 2, "not_judged"),
        ("check", 2, "passed"),
        ("publish", 1, "not_judged"),
    ]
    assert deps.seen == [
        ("draft", 1, ()),
        ("check", 1, ()),
        ("draft", 2, ("too short", "no title")),
        ("check", 2, ()),
        ("publish", 1, ()),
    ]
    assert (run.attempts("draft"), run.attempts("check"), run.attempts("publish")) == (2, 2, 1)


async def test_a_station_can_be_its_own_rework_target() -> None:
    deps = Deps(problems=[["x"]])

    run = await run_line(line(Rework("check")), Brief(goal="x"), store=MemoryStore(), deps=deps)

    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "failed"),
        ("check", 2, "passed"),
        ("publish", 1, "not_judged"),
    ]


async def test_at_the_rework_limit_the_line_halts_by_default() -> None:
    deps = Deps(problems=[["a"], ["b"], ["c"]])

    run = await run_line(
        line(Rework("check", "draft", limit=2)), Brief(goal="x"), store=MemoryStore(), deps=deps
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("failed", False, "check")
    assert [o for o in outcomes(run) if o[0] == "check"] == [
        ("check", 1, "failed"),
        ("check", 2, "failed"),
        ("check", 3, "failed"),
    ]
    latest = run.latest("check")
    assert latest is not None and latest.decision is not None
    assert latest.decision.reasons == ("c",)


async def test_at_the_limit_a_line_may_carry_on_but_the_failure_stays_on_the_record() -> None:
    deps = Deps(problems=[["a"], ["b"]])

    run = await run_line(
        line(Rework("check", "draft", limit=1, on_limit="continue")),
        Brief(goal="x"),
        store=MemoryStore(),
        deps=deps,
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("failed", True, None)
    assert outcomes(run)[-2:] == [("check", 2, "failed"), ("publish", 1, "not_judged")]
    assert run.output is not None


async def test_a_station_that_did_not_run_stops_the_line_and_is_never_reworked() -> None:
    deps = Deps(break_check=True)

    run = await run_line(
        line(Rework("check", "draft", limit=5)), Brief(goal="x"), store=MemoryStore(), deps=deps
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("did_not_run", False, "check")
    assert outcomes(run) == [("draft", 1, "not_judged"), ("check", 1, "did_not_run")]
    latest = run.latest("check")
    assert latest is not None
    assert latest.reason == "the station raised ConnectionError: the checker is unreachable"


async def test_a_gate_that_asks_for_a_person_stops_the_line_there() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return needs_approval("ready for sign-off")

    @station(gate=sign_off, name="check")
    async def approve_me(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    waiting = Line("document", [draft, approve_me, publish])

    run = await run_line(waiting, Brief(goal="x"), store=MemoryStore(), deps=Deps())

    assert (run.outcome, run.finished, run.stopped_at) == ("needs_approval", False, "check")
    assert run.latest("publish") is None


async def test_the_line_takes_an_artifact_and_refuses_the_wrong_input() -> None:
    store = MemoryStore()
    given = Artifact.create(Brief(goal="x"))

    run = await run_line(line(), given, store=store, deps=Deps())

    assert run.input == given.ref
    with pytest.raises(StationInputInvalid, match="line 'document' takes Brief, not Draft"):
        await run_line(line(), Draft(text="x"), store=store, deps=Deps())


def test_a_badly_formed_line_is_refused_with_every_finding_at_once() -> None:
    @station(name="draft")
    async def twin(ctx: StationContext, brief: Brief) -> Draft:
        return Draft(text="")

    with pytest.raises(LineDefinitionError) as caught:
        Line(
            " ",
            [draft, twin, publish, check],
            rework=[
                Rework("missing"),
                Rework("check", "nowhere"),
                Rework("check", "draft"),
                Rework("publish", "check", limit=0),
            ],
        )

    assert caught.value.findings == [
        "the name must be non-empty with no surrounding space",
        "station name 'draft' is used more than once",
        "'draft' hands on Draft but 'draft' takes Brief",
        "'draft' hands on Draft but 'publish' takes Checked",
        "'publish' hands on Published but 'check' takes Draft",
        "rework from 'missing': no such station",
        "rework from 'check': target 'nowhere' is not a station",
        "rework from 'check': declared more than once",
        "rework from 'publish': target 'check' comes later in the line",
        "rework from 'publish': the limit must be at least 1",
        "rework from 'publish': the station has no gate, so it can never fail",
    ]


def test_an_empty_line_is_refused() -> None:
    with pytest.raises(LineDefinitionError, match="a line needs at least one station"):
        Line("empty", [])


def test_a_line_knows_its_stations_and_rules() -> None:
    rule = Rework("check", "draft")
    built = line(rule)

    assert [s.name for s in built.stations] == ["draft", "check", "publish"]
    assert built.index_of("check") == 1
    assert built.rework_for("check") is rule
    assert built.rework_for("draft") is None
    assert (Rework("check").target, rule.target) == ("check", "draft")
