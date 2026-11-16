"""A factory moves work through its stations, sends failures back a bounded number of times,
and never retries a station that did not run."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    Factory,
    FactoryDefinitionError,
    MemoryStore,
    MethodologyRef,
    Rework,
    Run,
    StationContext,
    StationInputInvalid,
    Untyped,
    Verdict,
    failed,
    gate,
    needs_approval,
    passed,
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


def factory(*rework: Rework) -> Factory:
    return Factory("document", [draft, check, publish], rework=list(rework))


def outcomes(run: Any) -> list[tuple[str, int, str]]:
    return [(step.station, step.attempt, step.outcome) for step in run.steps]


async def test_work_moves_through_every_station_in_order() -> None:
    store = MemoryStore()
    stamp = MethodologyRef(name="review", version="1")

    run = await factory().run(
        Brief(goal="Split a bill"),
        store=store,
        deps=Deps(),
        run_id="run-1",
        mission_id="m-1",
        methodologies=[stamp],
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("passed", True, None)
    assert (run.run_id, run.factory, run.factory_version) == ("run-1", "document", "0")
    assert (run.mission_id, run.methodologies) == ("m-1", (stamp,))
    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "passed"),
        ("publish", 1, "not_judged"),
    ]
    assert run.output == Published(url="https://example.org/12")
    assert run.output_ref is not None
    assert (await store.get(run.output_ref, Published)).payload == run.output


async def test_each_output_is_the_next_stations_input_all_the_way_back() -> None:
    store = MemoryStore()

    run = await factory().run(Brief(goal="Split a bill"), store=store, deps=Deps())

    assert run.output_ref is not None
    published = await store.get(run.output_ref, Published)
    checked = await store.get(published.inputs[0], Checked)
    drafted = await store.get(checked.inputs[0], Draft)
    assert drafted.inputs == (run.input_ref,)
    assert {a.produced_by.run_id for a in (published, checked, drafted) if a.produced_by} == {
        run.run_id
    }


async def test_a_failure_with_no_rework_rule_stops_the_run() -> None:
    deps = Deps(problems=[["too short"]])

    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=deps)

    assert (run.outcome, run.finished, run.stopped_at, run.output) == (
        "failed",
        False,
        "check",
        None,
    )
    assert outcomes(run) == [("draft", 1, "not_judged"), ("check", 1, "failed")]


async def test_a_failure_sends_work_back_with_the_reasons_and_then_passes() -> None:
    deps = Deps(problems=[["too short", "no title"]])

    run = await factory(Rework(check, draft, limit=2)).run(
        Brief(goal="x"), store=MemoryStore(), deps=deps
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

    run = await factory(Rework(check)).run(Brief(goal="x"), store=MemoryStore(), deps=deps)

    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "failed"),
        ("check", 2, "passed"),
        ("publish", 1, "not_judged"),
    ]


async def test_at_the_rework_limit_the_run_halts_by_default() -> None:
    deps = Deps(problems=[["a"], ["b"], ["c"]])

    run = await factory(Rework(check, draft, limit=2)).run(
        Brief(goal="x"), store=MemoryStore(), deps=deps
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


async def test_at_the_limit_a_run_may_carry_on_but_the_failure_stays_on_the_record() -> None:
    deps = Deps(problems=[["a"], ["b"]])

    store = MemoryStore()
    run = await factory(Rework(check, draft, limit=1, on_limit="continue")).run(
        Brief(goal="x"),
        store=store,
        deps=deps,
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("failed", True, None)
    assert outcomes(run)[-2:] == [("check", 2, "failed"), ("publish", 1, "not_judged")]
    assert run.output_ref is not None
    failed_check = run.latest("check")
    assert failed_check is not None
    assert (await store.get(run.output_ref, Published)).inputs == (failed_check.output_ref,)


async def test_a_station_that_did_not_run_stops_the_run_and_is_never_reworked() -> None:
    deps = Deps(break_check=True)

    run = await factory(Rework(check, draft, limit=5)).run(
        Brief(goal="x"), store=MemoryStore(), deps=deps
    )

    assert (run.outcome, run.finished, run.stopped_at) == ("did_not_run", False, "check")
    assert outcomes(run) == [("draft", 1, "not_judged"), ("check", 1, "did_not_run")]
    latest = run.latest("check")
    assert latest is not None
    assert latest.reason == "the station raised ConnectionError: the checker is unreachable"


async def test_a_gate_that_asks_for_a_person_stops_the_run_there() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return needs_approval("ready for sign-off")

    @station(gate=sign_off, name="check")
    async def approve_me(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    waiting = Factory("document", [draft, approve_me, publish])

    run = await waiting.run(Brief(goal="x"), store=MemoryStore(), deps=Deps())

    assert (run.outcome, run.finished, run.stopped_at) == ("needs_approval", False, "check")
    assert run.latest("publish") is None


async def test_the_factory_takes_an_artifact_and_refuses_the_wrong_input() -> None:
    store = MemoryStore()
    given = Artifact.create(Brief(goal="x"))

    run = await factory().run(given, store=store, deps=Deps())

    assert run.input_ref == given.ref
    with pytest.raises(StationInputInvalid, match="factory 'document' expected Brief, not Draft"):
        await factory().run(Draft(text="x"), store=store, deps=Deps())


def test_a_badly_formed_factory_is_refused_with_every_finding_at_once() -> None:
    @station(name="draft")
    async def twin(ctx: StationContext, brief: Brief) -> Draft:
        return Draft(text="")

    @station(name="elsewhere")
    async def elsewhere(ctx: StationContext, brief: Brief) -> Draft:
        return Draft(text="")

    with pytest.raises(FactoryDefinitionError) as caught:
        Factory(
            " ",
            [draft, twin, publish, check],
            rework=[
                Rework(elsewhere),
                Rework(check, elsewhere),
                Rework(check, draft),
                Rework(publish, check, limit=0),
                Rework(elsewhere, limit=0, on_limit="stop"),  # type: ignore[arg-type]
            ],
        )

    assert caught.value.findings == [
        "the name must be non-empty with no surrounding space",
        "station name 'draft' is used more than once",
        "'draft' hands on Draft but 'draft' takes Brief",
        "'draft' hands on Draft but 'publish' takes Checked",
        "'publish' hands on Published but 'check' takes Draft",
        "rework from 'elsewhere': the station is not in this factory",
        "rework from 'check': the station it sends work to is not in this factory",
        "rework from 'check': declared more than once",
        "rework from 'publish': the limit must be a whole number, at least 1",
        "rework from 'publish': 'check' comes later in the factory",
        "rework from 'publish': the station has no gate, so it can never fail",
        "rework from 'elsewhere': the limit must be a whole number, at least 1",
        "rework from 'elsewhere': on_limit must be 'halt' or 'continue'",
        "rework from 'elsewhere': the station is not in this factory",
    ]


def test_an_empty_factory_is_refused() -> None:
    with pytest.raises(FactoryDefinitionError, match="a factory needs at least one station"):
        Factory("empty", [])


def test_other_malformed_factories_are_refused_with_a_finding_not_a_crash() -> None:
    text_limit: Any = "2"
    cases: list[tuple[dict[str, Any], str]] = [
        ({"version": ""}, "the version must be non-empty"),
        ({"name": "x" * 201}, "the name must be one line"),
        ({"stations": [draft, "check"]}, "every item must be a Station"),
        ({"rework": [Rework(check, limit=True)]}, "the limit must be a whole number"),
        ({"rework": [Rework(check, limit=text_limit)]}, "the limit must be a whole number"),
        ({"rework": ["check"]}, "every rework rule must be a Rework"),
    ]
    for change, message in cases:
        arguments: dict[str, Any] = {"name": "document", "stations": [draft, check, publish]}
        arguments.update(change)
        name, stations = arguments.pop("name"), arguments.pop("stations")
        with pytest.raises(FactoryDefinitionError, match=message):
            Factory(name, stations, **arguments)


def test_a_factory_knows_its_stations_and_rules() -> None:
    rule = Rework(check, draft)
    built = factory(rule)

    assert [s.name for s in built.stations] == ["draft", "check", "publish"]
    assert built.index_of("check") == 1
    assert built.rework_for("check") is rule
    assert built.rework_for("draft") is None
    assert Rework(check).target is check
    assert rule.target is draft


# --- two rework rules in one factory


@gate(version="1")
def publishable(output: Artifact[Published], evidence: Evidence) -> Verdict:
    return failed("bad address") if output.payload.url.endswith("/bad") else passed("ok")


@station(gate=publishable, name="publish")
async def fussy_publish(ctx: StationContext[Deps], checked: Checked) -> Published:
    ctx.deps.seen.append(("publish", ctx.attempt, ()))
    return Published(url="https://example.org/" + ("bad" if ctx.attempt == 1 else "good"))


def two_rule_factory(check_rule: Rework) -> Factory:
    return Factory(
        "document",
        [draft, check, fussy_publish],
        rework=[check_rule, Rework(fussy_publish, draft, limit=1)],
    )


async def test_a_failure_that_later_rework_put_right_does_not_fail_the_run() -> None:
    deps = Deps(problems=[["a"], ["b"]])

    run = await two_rule_factory(Rework(check, limit=1, on_limit="continue")).run(
        Brief(goal="x"),
        store=MemoryStore(),
        deps=deps,
    )

    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "failed"),
        ("check", 2, "failed"),
        ("publish", 1, "failed"),
        ("draft", 2, "not_judged"),
        ("check", 3, "passed"),
        ("publish", 2, "passed"),
    ]
    assert (run.outcome, run.finished) == ("passed", True)
    assert [seen for seen in deps.seen if seen[0] == "draft"] == [
        ("draft", 1, ()),
        ("draft", 2, ("bad address",)),
    ]


async def test_a_rework_limit_counts_for_the_whole_run() -> None:
    deps = Deps(problems=[["a"], [], ["b"]])

    run = await two_rule_factory(Rework(check, limit=1)).run(
        Brief(goal="x"), store=MemoryStore(), deps=deps
    )

    assert outcomes(run) == [
        ("draft", 1, "not_judged"),
        ("check", 1, "failed"),
        ("check", 2, "passed"),
        ("publish", 1, "failed"),
        ("draft", 2, "not_judged"),
        ("check", 3, "failed"),
    ]
    assert (run.outcome, run.stopped_at) == ("failed", "check")


# --- the record


async def test_the_record_survives_json_and_names_the_shape_of_its_factory() -> None:
    built = factory(Rework(check, draft))

    run = await built.run(Brief(goal="x"), store=MemoryStore(), deps=Deps())

    restored = type(run).model_validate_json(run.model_dump_json())
    # The output is typed Any on the record, so it reads back as JSON data, not a model.
    assert restored.model_dump(mode="json") == run.model_dump(mode="json")
    assert (restored.steps, restored.output_ref) == (run.steps, run.output_ref)
    assert run.factory_digest == built.digest
    assert built.digest == factory(Rework(check, draft)).digest
    assert built.digest != factory(Rework(check, draft, limit=2)).digest
    assert built.digest != factory().digest


async def test_every_artifact_a_run_makes_carries_its_mission_and_methodologies() -> None:
    store = MemoryStore()
    stamp = MethodologyRef(name="review", version="1")

    run = await factory().run(
        Brief(goal="x"), store=store, deps=Deps(), mission_id="m-1", methodologies=[stamp]
    )

    refs = [run.input_ref, *(step.output_ref for step in run.steps if step.output_ref)]
    for ref in refs:
        artifact = await store.get(ref, Untyped)
        assert (artifact.mission_id, artifact.methodologies) == ("m-1", (stamp,))


async def test_a_run_that_stops_early_has_no_output() -> None:
    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=Deps(break_check=True))

    assert (run.output, run.finished) == (None, False)


async def test_progress_can_be_saved_after_every_attempt() -> None:
    saved: list[Run] = []

    async def save(progress: Run) -> None:
        saved.append(progress)

    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=Deps(), on_step=save)

    assert [(s.outcome, s.finished, len(s.steps)) for s in saved] == [
        ("running", False, 1),
        ("running", False, 2),
        ("running", False, 3),
    ]
    assert saved[-1].steps == run.steps


async def test_bad_run_arguments_are_refused_before_any_station_runs() -> None:
    deps = Deps()

    bad: list[dict[str, Any]] = [{"run_id": " x"}, {"run_id": ""}, {"mission_id": " "}]
    for arguments in bad:
        with pytest.raises(StationInputInvalid, match=r"(run_id|mission_id) must be non-empty"):
            await factory().run(Brief(goal="x"), store=MemoryStore(), deps=deps, **arguments)
    assert deps.seen == []


def test_a_record_that_contradicts_itself_is_refused() -> None:
    ref = Artifact.create(Brief(goal="x")).ref
    common: dict[str, Any] = {
        "run_id": "r",
        "factory": "l",
        "factory_version": "0",
        "factory_digest": factory().digest,
        "input_ref": ref,
    }
    contradictions: list[dict[str, Any]] = [
        {"outcome": "passed", "finished": False, "stopped_at": "check"},
        {"outcome": "passed", "finished": True},
        {"outcome": "failed", "finished": False},
        {"outcome": "failed", "finished": True, "output_ref": ref, "stopped_at": "check"},
        {"outcome": "did_not_run", "finished": True, "output_ref": ref},
        {"outcome": "running", "finished": False, "stopped_at": "check"},
        {"outcome": "needs_approval", "finished": False, "stopped_at": "check", "output_ref": ref},
    ]
    for wrong in contradictions:
        with pytest.raises(ValidationError):
            Run(**common, **wrong)
