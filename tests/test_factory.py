"""A factory moves work through its stations, sends failures back a bounded number of times,
and never retries a station that did not run."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from contextlib import closing
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    Abandoned,
    Answer,
    Artifact,
    AskPerson,
    CannotAbandon,
    Evidence,
    Factory,
    FactoryDefinitionError,
    LocalStore,
    MemoryStore,
    Rework,
    Run,
    RunConflict,
    SQLiteStore,
    StationContext,
    StationInputInvalid,
    StoreSchemaMismatch,
    Verdict,
    Waiting,
    escalate,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.factory import FactoryShape, GateShape, ReworkShape
from pydantic_ai_factory_software import SoftwareFactory

pytestmark = pytest.mark.anyio

WHEN = datetime(2026, 11, 18, 9, 0, tzinfo=UTC)


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

    run = await factory().run(Brief(goal="Split a bill"), store=store, deps=Deps(), run_id="run-1")

    assert (run.outcome, run.finished, run.stopped_at) == ("passed", True, None)
    assert (run.run_id, run.factory, run.factory_version) == ("run-1", "document", "0")
    assert run.mission_id is None
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


async def test_a_gate_that_escalates_stops_the_run_to_wait_for_a_person() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return escalate("only a person can settle this")

    @station(gate=sign_off, name="check")
    async def approve_me(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    waiting = Factory("document", [draft, approve_me, publish])

    run = await waiting.run(Brief(goal="x"), store=MemoryStore(), deps=Deps())

    assert (run.outcome, run.finished, run.stopped_at) == ("waiting", False, "check")
    assert run.waiting is not None and run.waiting.kind == "escalation"
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
        "rework from 'elsewhere': on_limit must be 'halt', 'continue' or 'escalate'",
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


async def test_a_run_that_stops_early_has_no_output() -> None:
    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=Deps(break_check=True))

    assert (run.output, run.finished) == (None, False)


async def test_the_run_is_saved_after_every_step_and_on_step_watches_each_save() -> None:
    saved: list[Run] = []
    store = MemoryStore()

    async def watch(progress: Run) -> None:
        saved.append(progress)

    run = await factory().run(Brief(goal="x"), store=store, deps=Deps(), on_step=watch)

    assert [(s.outcome, s.finished, len(s.steps), s.revision) for s in saved] == [
        ("running", False, 0, 0),
        ("running", False, 1, 1),
        ("running", False, 2, 2),
        ("running", False, 3, 3),
        ("passed", True, 3, 4),
    ]
    stored = await store.get_run(run.run_id)
    assert (stored.revision, stored.steps) == (run.revision, run.steps)


async def test_bad_run_arguments_are_refused_before_any_station_runs() -> None:
    deps = Deps()

    bad: list[dict[str, Any]] = [{"run_id": " x"}, {"run_id": ""}]
    for arguments in bad:
        with pytest.raises(StationInputInvalid, match=r"run_id must be non-empty"):
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
        "created_at": WHEN,
        "updated_at": WHEN,
    }
    waits = Waiting(kind="approval", station="check")
    abandoned = Abandoned(reason="the worker died", by="ops", at=WHEN)
    contradictions: list[dict[str, Any]] = [
        {"outcome": "passed", "finished": False, "stopped_at": "check", "station": None},
        {"outcome": "passed", "finished": True, "station": None},
        {"outcome": "failed", "finished": False, "station": None},
        {"outcome": "failed", "finished": True, "output_ref": ref, "stopped_at": "check"}
        | {"station": None},
        {"outcome": "did_not_run", "finished": True, "output_ref": ref, "station": None},
        {"outcome": "running", "finished": False, "stopped_at": "check", "station": "check"},
        {"outcome": "waiting", "finished": False, "stopped_at": "check", "station": "check"},
        # Where the run is: named exactly while it has not ended, and where a wait stopped.
        {"outcome": "running", "finished": False, "station": None},
        {"outcome": "passed", "finished": True, "output_ref": ref, "station": "publish"},
        {"outcome": "waiting", "finished": False, "stopped_at": "check", "waiting": waits}
        | {"station": "draft"},
        # A waiting run waits at the station it is at.
        {"outcome": "waiting", "finished": False, "stopped_at": "draft", "station": "draft"}
        | {"waiting": waits},
        # Only a run that did not run was abandoned.
        {"outcome": "failed", "finished": False, "stopped_at": "check", "station": None}
        | {"abandoned": abandoned},
    ]
    sound: list[dict[str, Any]] = [
        {"outcome": "running", "finished": False, "station": "check"},
        {"outcome": "waiting", "finished": False, "stopped_at": "check", "waiting": waits}
        | {"station": "check"},
        {"outcome": "did_not_run", "finished": False, "stopped_at": "check", "station": None}
        | {"abandoned": abandoned},
    ]
    for right in sound:
        Run(**common, **right)
    for wrong in contradictions:
        with pytest.raises(ValidationError):
            Run(**common, **wrong)


class Clock:
    """Stands in for the runner's clock: each reading is one minute after the last."""

    def __init__(self) -> None:
        self.readings: list[datetime] = []

    def __call__(self) -> datetime:
        reading = datetime(2026, 11, 18, 9, 0, tzinfo=UTC) + timedelta(minutes=len(self.readings))
        self.readings.append(reading)
        return reading


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr("pydantic_ai_factory.factory._runner._now", clock)
    return clock


async def test_a_run_keeps_when_it_was_created_while_each_save_moves_when_it_last_changed(
    clock: Clock,
) -> None:
    saved: list[Run] = []

    async def watch(progress: Run) -> None:
        saved.append(progress)

    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=Deps(), on_step=watch)

    assert len(saved) == 5
    assert {s.created_at for s in saved} == {clock.readings[0]}
    assert [s.updated_at for s in saved] == clock.readings
    assert (run.created_at, run.updated_at) == (clock.readings[0], clock.readings[-1])


async def test_a_runs_times_are_in_utc_even_when_read_back_from_json() -> None:
    before = datetime.now(UTC)

    run = await factory().run(Brief(goal="x"), store=MemoryStore(), deps=Deps())

    restored = Run.model_validate_json(run.model_dump_json())
    for when in (run.created_at, run.updated_at, restored.created_at, restored.updated_at):
        assert when.utcoffset() == timedelta(0)
        assert before <= when <= datetime.now(UTC)


async def test_a_resumed_run_keeps_its_created_at_and_moves_its_updated_at(clock: Clock) -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return escalate("only a person can settle this")

    @station(gate=sign_off, name="check")
    async def approve_me(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    waiting_factory = Factory("document", [draft, approve_me, publish])
    store = MemoryStore()
    waiting = await waiting_factory.run(Brief(goal="x"), store=store, deps=Deps())
    assert waiting.outcome == "waiting"
    # A waiting run's last save is when it started waiting.
    assert waiting.updated_at == clock.readings[-1]

    done = await waiting_factory.resume(waiting.approve(by="ana"), store=store, deps=Deps())

    assert done.outcome == "passed"
    assert done.created_at == waiting.created_at == clock.readings[0]
    assert done.updated_at == clock.readings[-1] > waiting.updated_at
    assert (await store.get_run(done.run_id)).created_at == clock.readings[0]


async def test_a_run_saved_by_0_1_without_times_is_refused_with_a_schema_mismatch(
    tmp_path: Path,
) -> None:
    store = LocalStore(tmp_path)
    run = await factory().run(Brief(goal="x"), store=store, deps=Deps())
    # Rewrite the saved file as 0.1 would have written it: with neither time.
    path = store.run_path_for(run.run_id)
    saved_by_0_1 = json.loads(path.read_bytes())
    del saved_by_0_1["created_at"], saved_by_0_1["updated_at"]
    path.write_text(json.dumps(saved_by_0_1))

    with pytest.raises(StoreSchemaMismatch, match="another version"):
        await store.get_run(run.run_id)


# --- where a run is


async def saves_of(built: Factory, deps: Deps, store: MemoryStore | None = None) -> list[Run]:
    saved: list[Run] = []

    async def watch(progress: Run) -> None:
        saved.append(progress)

    await built.run(Brief(goal="x"), store=store or MemoryStore(), deps=deps, on_step=watch)
    return saved


async def test_a_running_run_names_the_station_it_is_at_before_that_station_finishes() -> None:
    saved = await saves_of(factory(), Deps())

    # Each save names the station about to run, and holds no result from it yet.
    assert [(s.outcome, s.station, [step.station for step in s.steps]) for s in saved] == [
        ("running", "draft", []),
        ("running", "check", ["draft"]),
        ("running", "publish", ["draft", "check"]),
        ("running", "publish", ["draft", "check", "publish"]),
        ("passed", None, ["draft", "check", "publish"]),
    ]


async def test_after_rework_the_run_names_the_station_the_work_went_back_to() -> None:
    saved = await saves_of(factory(Rework(check, draft)), Deps(problems=[["too long"]]))

    after_failure = next(s for s in saved if s.steps and s.steps[-1].outcome == "failed")
    assert after_failure.station == "draft"
    assert [s.station for s in saved] == [
        "draft",
        "check",
        "draft",
        "check",
        "publish",
        "publish",
        None,
    ]


async def test_a_run_that_ended_has_no_station() -> None:
    store = MemoryStore()
    passed_run = await factory().run(Brief(goal="x"), store=store, deps=Deps())
    failed_run = await factory().run(Brief(goal="x"), store=store, deps=Deps(problems=[["no"]]))
    not_run = await factory().run(Brief(goal="x"), store=store, deps=Deps(break_check=True))

    for run in (passed_run, failed_run, not_run):
        assert run.station is None
        assert (await store.get_run(run.run_id)).station is None
    assert [r.outcome for r in (passed_run, failed_run, not_run)] == [
        "passed",
        "failed",
        "did_not_run",
    ]
    assert (failed_run.stopped_at, not_run.stopped_at) == ("check", "check")


async def test_a_waiting_run_is_at_the_station_where_it_stopped() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return escalate("only a person can settle this")

    @station(gate=sign_off, name="check")
    async def settle(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    run = await Factory("document", [draft, settle, publish]).run(
        Brief(goal="x"), store=MemoryStore(), deps=Deps()
    )

    assert (run.outcome, run.station, run.stopped_at) == ("waiting", "check", "check")


async def test_a_run_that_runs_out_of_rework_and_escalates_waits_at_the_failed_station() -> None:
    built = factory(Rework(check, draft, limit=1, on_limit="escalate"))

    saved = await saves_of(built, Deps(problems=[["too long"], ["still too long"]]))

    # Out of rework, the run is saved running at the failed station, then waits there.
    assert [(s.outcome, s.station) for s in saved][-2:] == [
        ("running", "check"),
        ("waiting", "check"),
    ]
    assert saved[-1].waiting is not None and saved[-1].waiting.station == "check"


def a_person_agent(name: str, *replies: ModelResponse | str, **options: Any) -> Agent[None, str]:
    """An agent whose model gives ``replies`` in turn, then repeats the last one."""
    queue = list(replies)

    def reply(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        given = queue.pop(0) if len(queue) > 1 else queue[0]
        return given if isinstance(given, ModelResponse) else ModelResponse(parts=[TextPart(given)])

    return Agent(FunctionModel(reply), name=name, **options)


@station
async def build(ctx: StationContext[None], design: str) -> str:
    return f"built: {design}"


async def resumed_saves(built: Factory, answer_of: Callable[[Run], Answer]) -> list[Run]:
    """Run ``built`` until it waits, answer it, and return every save of the resume."""
    store = MemoryStore()
    waiting = await built.run("A to-do list page.", store=store)
    assert waiting.outcome == "waiting"
    saved: list[Run] = []

    async def watch(progress: Run) -> None:
        saved.append(progress)

    await built.resume(answer_of(waiting), store=store, on_step=watch)
    return saved


async def test_approving_a_middle_station_claims_the_run_at_the_next_station() -> None:
    designer = a_person_agent("designer", "One page.")
    built = Factory("software", [station(designer, approval=True), build])

    saved = await resumed_saves(built, lambda run: run.approve(by="ada"))

    assert (saved[0].outcome, saved[0].station) == ("running", "build")
    assert [step.station for step in saved[0].steps] == ["designer"]
    assert (saved[-1].outcome, saved[-1].station) == ("passed", None)


async def test_approving_the_last_station_claims_the_run_at_that_station() -> None:
    designer = a_person_agent("designer", "One page.")
    built = Factory("software", [station(designer, approval=True)])

    saved = await resumed_saves(built, lambda run: run.approve(by="ada"))

    assert [(s.outcome, s.station) for s in saved] == [
        ("running", "designer"),
        ("passed", None),
    ]


async def test_asking_for_changes_claims_the_run_at_the_waiting_station() -> None:
    designer = a_person_agent("designer", "One page.", "Two pages.")
    built = Factory("software", [station(designer, approval=True), build])

    saved = await resumed_saves(built, lambda run: run.request_changes(by="ada", note="More."))

    assert [(s.outcome, s.station) for s in saved] == [
        ("running", "designer"),
        ("waiting", "designer"),
    ]


async def test_replying_to_tool_calls_claims_the_run_at_the_waiting_station() -> None:
    question = ModelResponse(parts=[ToolCallPart("ask_person", {"question": "Colour?"}, "q1")])
    asker = a_person_agent("asker", question, "Blue.", capabilities=[AskPerson()])
    built = Factory("software", [station(asker), build])

    def blue(run: Run) -> Answer:
        return run.reply("Blue.", by="ada")

    saved = await resumed_saves(built, blue)

    assert (saved[0].outcome, saved[0].station) == ("running", "asker")
    assert [(s.outcome, s.station) for s in saved[1:]] == [
        ("running", "build"),
        ("running", "build"),
        ("passed", None),
    ]


# --- ending a run that is stuck


class WorkerDied(Exception):
    """Stands in for a worker that stops part-way through a run."""


async def stuck_at_check(built: Factory, store: MemoryStore) -> Run:
    """Run until the save that names ``check``, then stop, as a dead worker would."""

    async def die(progress: Run) -> None:
        if progress.station == "check":
            raise WorkerDied

    with pytest.raises(WorkerDied):
        await built.run(Brief(goal="x"), store=store, deps=Deps(), run_id="stuck", on_step=die)
    return await store.get_run("stuck")


async def test_abandon_ends_a_running_run_as_did_not_run_with_its_reason(clock: Clock) -> None:
    built, store = factory(), MemoryStore()
    stuck = await stuck_at_check(built, store)
    assert (stuck.outcome, stuck.station) == ("running", "check")

    ended = await built.abandon(stuck, reason="the worker died", by="ops", store=store)

    assert (ended.outcome, ended.finished, ended.station, ended.stopped_at) == (
        "did_not_run",
        False,
        None,
        "check",
    )
    assert ended.abandoned == Abandoned(reason="the worker died", by="ops", at=clock.readings[-1])
    assert (ended.revision, ended.updated_at) == (stuck.revision + 1, clock.readings[-1])
    assert ended.steps == stuck.steps
    assert await store.get_run("stuck") == ended


async def test_a_save_after_abandon_fails_with_run_conflict() -> None:
    built, store = factory(), MemoryStore()
    ended: list[Run] = []

    async def abandon_from_elsewhere(progress: Run) -> None:
        # Someone ends the run while its worker is still driving it.
        if progress.station == "check" and not ended:
            ended.append(await built.abandon(progress, reason="stalled", by="ops", store=store))

    with pytest.raises(RunConflict, match="moved on"):
        await built.run(
            Brief(goal="x"), store=store, deps=Deps(), run_id="slow", on_step=abandon_from_elsewhere
        )

    assert await store.get_run("slow") == ended[0]
    assert ended[0].outcome == "did_not_run"


async def test_abandon_refuses_a_run_that_is_not_running() -> None:
    built, store = factory(), MemoryStore()
    finished = await built.run(Brief(goal="x"), store=store, deps=Deps())

    with pytest.raises(CannotAbandon, match="not running"):
        await built.abandon(finished, reason="no", by="ops", store=store)

    stuck = await stuck_at_check(built, store)
    with pytest.raises(CannotAbandon, match="reason"):
        await built.abandon(stuck, reason="  ", by="ops", store=store)
    assert await store.get_run("stuck") == stuck


async def test_abandon_refuses_a_waiting_run_that_its_own_factory_can_resume() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return escalate("only a person can settle this")

    @station(gate=sign_off, name="check")
    async def settle(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    store = MemoryStore()
    same = Factory("document", [draft, settle, publish])
    waiting = await same.run(Brief(goal="x"), store=store, deps=Deps())

    with pytest.raises(CannotAbandon, match="'waiting'"):
        await same.abandon(waiting, reason="no", by="ops", store=store)

    # Nobody can answer a run that a factory of another shape made, so it may end.
    other = Factory("document", [draft, settle, publish], version="2")
    ended = await other.abandon(waiting, reason="the factory changed", by="ops", store=store)
    assert (ended.outcome, ended.stopped_at, ended.station, ended.waiting) == (
        "did_not_run",
        "check",
        None,
        None,
    )


async def test_a_factory_cannot_abandon_another_factorys_waiting_run() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Checked], evidence: Evidence) -> Verdict:
        return escalate("only a person can settle this")

    @station(gate=sign_off, name="check")
    async def settle(ctx: StationContext[Deps], draft: Draft) -> Checked:
        return Checked(text=draft.text)

    store = MemoryStore()
    waiting = await Factory("document", [draft, settle, publish]).run(
        Brief(goal="x"), store=store, deps=Deps()
    )
    stranger = Factory("leaflet", [draft, settle, publish])

    with pytest.raises(CannotAbandon, match="'waiting'"):
        await stranger.abandon(waiting, reason="not mine", by="ops", store=store)
    stored = await store.get_run(waiting.run_id)
    assert (stored.outcome, stored.revision) == ("waiting", waiting.revision)


def test_a_run_can_be_abandoned_from_code_that_is_not_async() -> None:
    built, store = factory(), MemoryStore()

    async def die(progress: Run) -> None:
        if progress.station == "check":
            raise WorkerDied

    with pytest.raises(WorkerDied):
        built.run_sync(Brief(goal="x"), store=store, deps=Deps(), run_id="stuck", on_step=die)

    ended = built.abandon_sync(store.get_run_sync("stuck"), reason="died", by="ops", store=store)
    assert (ended.outcome, ended.stopped_at) == ("did_not_run", "check")


async def test_abandon_refuses_a_run_that_moved_on_since_it_was_read() -> None:
    built, store = factory(), MemoryStore()
    stuck = await stuck_at_check(built, store)
    await built.abandon(stuck, reason="first", by="ops", store=store)

    with pytest.raises(RunConflict, match="moved on"):
        await built.abandon(stuck, reason="second", by="board", store=store)


@pytest.mark.parametrize("kind", ["local", "sqlite"])
async def test_a_run_saved_without_station_is_refused_with_store_schema_mismatch(
    kind: str, tmp_path: Path
) -> None:
    store = LocalStore(tmp_path) if kind == "local" else SQLiteStore(tmp_path / "factory.db")
    run = await factory().run(Brief(goal="x"), store=store, deps=Deps())
    saved = run.model_dump(mode="json")
    del saved["station"]
    data = json.dumps(saved).encode()
    if isinstance(store, LocalStore):
        store.run_path_for(run.run_id).write_bytes(data)
    else:
        with closing(sqlite3.connect(store.path)) as connection, connection:
            connection.execute("UPDATE runs SET data = ? WHERE run_id = ?", (data, run.run_id))

    with pytest.raises(StoreSchemaMismatch, match="station"):
        await store.get_run(run.run_id)


# --- what a factory looks like


def test_the_software_factorys_digest_is_unchanged() -> None:
    # Every saved run records this digest. If it changes, runs saved before the change
    # can no longer be resumed, so a change must be deliberate: update the value here.
    assert SoftwareFactory(TestModel()).digest == (
        "sha256:77065d5d15510bed795eee84e0bdfd2be1fe252ead88b0f5da1f3f697b50c6f6"
    )


def test_the_shape_matches_the_factory() -> None:
    built = factory(Rework(check, draft, limit=2, on_limit="escalate"))

    shape = built.shape()

    assert (shape.name, shape.version, shape.digest) == ("document", "0", built.digest)
    assert [(s.name, s.version, s.input, s.output) for s in shape.stations] == [
        ("draft", "0", "Brief", "Draft"),
        ("check", "0", "Draft", "Checked"),
        ("publish", "0", "Checked", "Published"),
    ]
    assert [s.gate for s in shape.stations] == [
        None,
        GateShape(name="no_problems", version="1"),
        None,
    ]
    assert shape.rework == (ReworkShape(station="check", to="draft", limit=2, on_limit="escalate"),)
    assert FactoryShape.model_validate_json(shape.model_dump_json()) == shape

    software = SoftwareFactory(TestModel()).shape()
    assert software.digest == SoftwareFactory(TestModel()).digest
    (design,) = software.stations
    assert (design.name, design.agents, design.approval) == ("design", ("designer",), True)
    assert design.methodologies == tuple(
        str(m.ref) for m in SoftwareFactory(TestModel()).stations[0].methodologies
    )
    assert software.rework == (
        ReworkShape(station="design", to="design", limit=2, on_limit="escalate"),
    )
