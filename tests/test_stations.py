"""A station keeps "did it run" apart from "did it pass"."""

from __future__ import annotations

import asyncio
import dataclasses
from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from pydantic_ai_factory import (
    Artifact,
    DefinitionError,
    Evidence,
    Feedback,
    MemoryStore,
    MethodologyRef,
    Producer,
    Station,
    StationClosed,
    StationContext,
    StationDefinitionError,
    StationInputInvalid,
    StationResult,
    Verdict,
    failed,
    gate,
    needs_approval,
    passed,
    station,
)

pytestmark = pytest.mark.anyio


class Text(BaseModel):
    body: str


class Count(BaseModel):
    words: int


class Note(BaseModel):
    text: str


class NotSerialisable:
    """A plain class with no schema, which Pydantic cannot serialise."""


@gate(version="1")
def under_ten_words(output: Artifact[Count], evidence: Evidence) -> Verdict:
    if output.payload.words >= 10:
        return failed(f"{output.payload.words} words is not under 10")
    return passed("short enough")


@station(gate=under_ten_words, version="3")
async def count_words(ctx: StationContext, text: Text) -> Count:
    return Count(words=len(text.body.split()))


def test_the_decorator_reads_the_name_and_types_from_the_function() -> None:
    assert isinstance(count_words, Station)
    assert (count_words.name, count_words.version) == ("count_words", "3")
    assert (count_words.input_type, count_words.output_type) == (Text, Count)
    assert count_words.gate is under_ten_words


async def test_a_station_that_passes_its_gate_ran_and_passed() -> None:
    store = MemoryStore()

    result = await count_words.run(Text(body="one two three"), store=store, run_id="r1")

    assert (result.outcome, result.ran, result.passed) == ("passed", True, True)
    assert (result.station, result.station_version, result.run_id, result.attempt) == (
        "count_words",
        "3",
        "r1",
        1,
    )
    assert result.output_ref is not None and result.decision is not None
    assert result.decision.reasons == ("short enough",)
    assert result.reason is None
    output = await store.get(result.output_ref, Count)
    assert output.payload == Count(words=3)


async def test_a_station_that_fails_its_gate_ran_and_did_not_pass() -> None:
    result = await count_words.run(Text(body="w " * 12), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("failed", True, False)
    assert result.decision is not None
    assert result.decision.reasons == ("12 words is not under 10",)
    assert result.output_ref is not None


async def test_a_station_that_raises_did_not_run_and_did_not_fail() -> None:
    @station(gate=under_ten_words)
    async def broken(ctx: StationContext, text: Text) -> Count:
        raise ConnectionError("the tool is unreachable")

    result = await broken.run(Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("did_not_run", False, None)
    assert result.reason == "the station raised ConnectionError: the tool is unreachable"
    assert result.output_ref is None and result.decision is None


async def test_a_station_that_returns_the_wrong_type_did_not_run() -> None:
    @station
    async def wrong(ctx: StationContext, text: Text) -> Count:
        return text  # type: ignore[return-value]

    result = await wrong.run(Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran) == ("did_not_run", False)
    assert result.reason is not None
    assert result.reason.startswith("the station returned the wrong type")


async def test_a_station_with_no_gate_ran_but_was_not_judged() -> None:
    @station
    async def ungated(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await ungated.run(Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("not_judged", True, None)
    assert result.output_ref is not None and result.decision is None


async def test_missing_evidence_means_the_gate_did_not_run_and_nothing_passed() -> None:
    @gate(version="1", requires=[Note])
    def needs_a_note(output: Artifact[Count], evidence: Evidence) -> Verdict:
        return passed("ok")

    @station(gate=needs_a_note)
    async def forgetful(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await forgetful.run(Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("did_not_run", True, None)
    assert result.reason == "gate 'needs_a_note' was not evaluated: missing evidence: Note"
    assert result.output_ref is not None and result.decision is None


async def test_a_gate_that_asks_for_a_person_leaves_passed_unanswered() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Count], evidence: Evidence) -> Verdict:
        return needs_approval("ready for sign-off")

    @station(gate=sign_off)
    async def drafted(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await drafted.run(Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("needs_approval", True, None)
    assert result.decision is not None and result.decision.judgement == "needs_approval"


async def test_recorded_evidence_is_stored_with_lineage_and_reaches_the_gate() -> None:
    @gate(version="1", requires=[Note])
    def note_present(output: Artifact[Count], evidence: Evidence) -> Verdict:
        return passed(evidence.one(Note).payload.text)

    @station(gate=note_present, version="2")
    async def careful(ctx: StationContext, text: Text) -> Count:
        await ctx.record(Note(text="counted by splitting on spaces"))
        return Count(words=len(text.body.split()))

    store = MemoryStore()
    stamp = MethodologyRef(name="review", version="1")
    given = Artifact.create(Text(body="a b"))

    result = await careful.run(
        given, store=store, run_id="r9", mission_id="m-1", methodologies=[stamp]
    )

    assert result.outcome == "passed"
    assert result.decision is not None and result.output_ref is not None
    assert result.decision.reasons == ("counted by splitting on spaces",)
    assert result.decision.methodologies == (stamp,)
    (note_ref,) = result.evidence
    assert result.decision.judged == (result.output_ref, note_ref)

    producer = Producer(station="careful", station_version="2", run_id="r9", attempt=1)
    for ref, payload_type in ((note_ref, Note), (result.output_ref, Count)):
        artifact = await store.get(ref, payload_type)
        assert artifact.produced_by == producer
        assert artifact.inputs == (given.ref,)
        assert (artifact.mission_id, artifact.methodologies) == ("m-1", (stamp,))
    assert await store.exists(given.ref)


async def test_the_context_carries_deps_the_attempt_and_feedback() -> None:
    @dataclass
    class Deps:
        greeting: str

    seen: dict[str, Any] = {}

    @station
    async def curious(ctx: StationContext[Deps], text: Text) -> Count:
        seen.update(
            greeting=ctx.deps.greeting,
            attempt=ctx.attempt,
            feedback=ctx.feedback,
            run_id=ctx.run_id,
            input=text,
        )
        return Count(words=0)

    earlier = Feedback(reasons=("12 words is not under 10",))

    result = await curious.run(
        Text(body="hi"),
        store=MemoryStore(),
        deps=Deps(greeting="hello"),
        run_id="r2",
        attempt=2,
        feedback=earlier,
    )

    assert result.attempt == 2
    assert seen == {
        "greeting": "hello",
        "attempt": 2,
        "feedback": earlier,
        "run_id": "r2",
        "input": Text(body="hi"),
    }


async def test_the_wrong_input_type_is_refused_before_anything_runs() -> None:
    store = MemoryStore()

    ran: list[str] = []

    @station
    async def watched(ctx: StationContext, text: Text) -> Count:
        ran.append("ran")
        return Count(words=0)

    bad_calls: list[tuple[Any, dict[str, Any], str]] = [
        (Count(words=1), {}, "station 'watched' expected Text, not Count"),
        ("hello", {}, "station 'watched' expected Text, not str"),
        (Text(body="x"), {"attempt": 0}, "attempt"),
        (Text(body="x"), {"run_id": " padded"}, "run_id"),
        (Text(body="x"), {"mission_id": " "}, "mission_id must be non-empty"),
    ]
    for given, arguments, message in bad_calls:
        with pytest.raises(StationInputInvalid, match=message):
            await watched.run(given, store=store, **arguments)
    assert (ran, len(store)) == ([], 0)


def test_a_definition_error_lists_every_finding() -> None:
    with pytest.raises(StationDefinitionError) as caught:
        Station(
            name=" ",
            function=count_words.function,
            input_type=Text,
            output_type=Count,
            version="",
        )

    assert isinstance(caught.value, DefinitionError)
    assert caught.value.subject == "station ' '"
    assert caught.value.findings == [
        "the name must be non-empty with no surrounding space",
        "the version must be non-empty with no surrounding space",
    ]


def test_a_badly_declared_station_is_refused_with_the_reason() -> None:
    declare: Any = station  # typed loosely, because each of these is wrong on purpose

    async def unannotated(ctx, text):  # type: ignore[no-untyped-def]
        return Count(words=0)

    async def no_input(ctx: StationContext) -> Count:
        return Count(words=0)

    async def not_serialisable(ctx: StationContext, text: NotSerialisable) -> Count:
        return Count(words=0)

    def synchronous(ctx: StationContext, text: Text) -> Count:
        return Count(words=0)

    async def unresolvable(ctx: StationContext, text: Missing) -> Count:  # type: ignore[name-defined]  # noqa: F821
        return Count(words=0)

    async def keyword_only(ctx: StationContext, *, text: Text) -> Count:
        return Count(words=0)

    cases: list[tuple[Any, str]] = [
        (keyword_only, "exactly two positional arguments"),
        (42, "declared from a function"),
        (unannotated, "annotate the input and the return type"),
        (no_input, "exactly two positional arguments"),
        (not_serialisable, "input type must be one Pydantic can serialise"),
        (synchronous, "the function must be async"),
        (unresolvable, "cannot resolve the annotation 'Missing'"),
    ]
    for function, message in cases:
        with pytest.raises(StationDefinitionError, match=message):
            declare(function)

    bad_fields: list[dict[str, Any]] = [
        {"name": " "},
        {"name": "two" + chr(10) + "lines"},
        {"name": "x" * 201},
        {"version": ""},
    ]
    for bad in bad_fields:
        fields: dict[str, Any] = {
            "name": "ok",
            "function": count_words.function,
            "input_type": Text,
            "output_type": Count,
            **bad,
        }
        with pytest.raises(StationDefinitionError, match=r"the (name|version) must be"):
            Station(**fields)


async def test_station_code_cannot_change_the_record_of_its_own_run() -> None:
    @station(gate=under_ten_words)
    async def tamper(ctx: StationContext, text: Text) -> Count:
        for name, value in (("run_id", "forged"), ("attempt", 9), ("feedback", None)):
            with pytest.raises(dataclasses.FrozenInstanceError):
                setattr(ctx, name, value)
        await ctx.record(Note(text="kept"))
        return Count(words=1)

    store = MemoryStore()
    result = await tamper.run(Text(body="x"), store=store, run_id="real", attempt=2)

    assert (result.outcome, result.run_id, result.attempt, len(result.evidence)) == (
        "passed",
        "real",
        2,
        1,
    )
    assert result.output_ref is not None
    produced_by = (await store.get(result.output_ref, Count)).produced_by
    assert produced_by == Producer(station="tamper", run_id="real", attempt=2)


async def test_evidence_cannot_be_recorded_after_the_station_returns() -> None:
    kept: list[StationContext[Any]] = []

    @station
    async def leaky(ctx: StationContext, text: Text) -> Count:
        kept.append(ctx)
        return Count(words=1)

    await leaky.run(Text(body="x"), store=MemoryStore())

    with pytest.raises(StationClosed, match="record evidence before returning"):
        await kept[0].record(Note(text="too late"))


async def test_a_bug_in_a_station_is_reported_with_its_type_traceback_and_a_log_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    @station
    async def buggy(ctx: StationContext, text: Text) -> Count:
        return Count(words=text.nope)  # type: ignore[attr-defined]

    with caplog.at_level("ERROR", logger="pydantic_ai_factory"):
        result = await buggy.run(Text(body="x"), store=MemoryStore())

    assert (result.outcome, result.error_type) == ("did_not_run", "AttributeError")
    assert result.traceback is not None and "text.nope" in result.traceback
    assert caplog.records[0].exc_info is not None


async def test_evidence_recorded_before_a_station_raises_is_kept() -> None:
    @station
    async def half_done(ctx: StationContext, text: Text) -> Count:
        await ctx.record(Note(text="got this far"))
        raise TimeoutError("too slow")

    result = await half_done.run(Text(body="x"), store=MemoryStore())

    assert (result.outcome, result.error_type, len(result.evidence)) == (
        "did_not_run",
        "TimeoutError",
        1,
    )


async def test_cancellation_is_not_swallowed() -> None:
    @station
    async def cancelled(ctx: StationContext, text: Text) -> Count:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled.run(Text(body="x"), store=MemoryStore())


async def test_an_output_that_cannot_be_recorded_did_not_run() -> None:
    class Ratio(BaseModel):
        value: float

    @station
    async def divide(ctx: StationContext, text: Text) -> Ratio:
        return Ratio(value=float("nan"))

    result = await divide.run(Text(body="x"), store=MemoryStore())

    assert (result.outcome, result.ran, result.error_type) == (
        "did_not_run",
        False,
        "ArtifactInvalid",
    )
    assert result.reason is not None
    assert result.reason.startswith("the station's output cannot be recorded")


async def test_a_subclass_of_the_output_type_is_accepted() -> None:
    class DetailedCount(Count):
        characters: int

    @station(gate=under_ten_words)
    async def detailed(ctx: StationContext, text: Text) -> Count:
        return DetailedCount(words=1, characters=len(text.body))

    store = MemoryStore()
    result = await detailed.run(Text(body="hello"), store=store)

    assert result.outcome == "passed" and result.output_ref is not None
    assert (await store.get(result.output_ref, DetailedCount)).payload.characters == 5


async def test_each_run_gets_its_own_id_and_names_its_input() -> None:
    store = MemoryStore()
    given = Artifact.create(Text(body="x"))

    first = await count_words.run(given, store=store)
    second = await count_words.run(given, store=store)

    assert first.run_id != second.run_id
    assert first.input_ref == second.input_ref == given.ref


async def test_recorded_evidence_keeps_its_type_label() -> None:
    @station
    async def labelled(ctx: StationContext, text: Text) -> Count:
        await ctx.record(Note(text="n"), type="ReviewNote")
        return Count(words=1)

    store = MemoryStore()
    result = await labelled.run(Text(body="x"), store=store)

    (ref,) = result.evidence
    assert ref.type == "ReviewNote"
    assert (await store.get(ref, Note)).inputs == (result.input_ref,)


def test_a_result_that_contradicts_itself_is_refused() -> None:
    ref = Artifact.create(Text(body="x")).ref
    common: dict[str, Any] = {
        "station": "s",
        "station_version": "0",
        "run_id": "r",
        "attempt": 1,
        "input_ref": ref,
    }

    contradictions: list[dict[str, Any]] = [
        {"outcome": "passed", "ran": False, "passed": False},
        {"outcome": "passed", "ran": True, "passed": True},
        {"outcome": "not_judged", "ran": True, "passed": None},
        {"outcome": "did_not_run", "ran": False, "passed": None},
        {"outcome": "did_not_run", "ran": True, "passed": None, "reason": "x"},
    ]
    for wrong in contradictions:
        with pytest.raises(ValidationError):
            StationResult(**common, **wrong)
