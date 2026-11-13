"""A station keeps "did it run" apart from "did it pass"."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    MemoryStore,
    MethodologyRef,
    Producer,
    Station,
    StationContext,
    StationDefinitionError,
    StationInputInvalid,
    Verdict,
    evaluate_gate,
    failed,
    gate,
    needs_approval,
    passed,
    run_station,
    station,
)
from pydantic_ai_factory.gates import GateDecision

pytestmark = pytest.mark.anyio


class Text(BaseModel):
    body: str


class Count(BaseModel):
    words: int


class Note(BaseModel):
    text: str


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

    result = await run_station(count_words, Text(body="one two three"), store=store, run_id="r1")

    assert (result.outcome, result.ran, result.passed) == ("passed", True, True)
    assert (result.station, result.station_version, result.run_id, result.attempt) == (
        "count_words",
        "3",
        "r1",
        1,
    )
    assert result.output is not None and result.decision is not None
    assert result.decision.reasons == ("short enough",)
    assert result.reason is None
    output = await store.get(result.output, Count)
    assert output.payload == Count(words=3)


async def test_a_station_that_fails_its_gate_ran_and_did_not_pass() -> None:
    result = await run_station(count_words, Text(body="w " * 12), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("failed", True, False)
    assert result.decision is not None
    assert result.decision.reasons == ("12 words is not under 10",)
    assert result.output is not None


async def test_a_station_that_raises_did_not_run_and_did_not_fail() -> None:
    @station(gate=under_ten_words)
    async def broken(ctx: StationContext, text: Text) -> Count:
        raise ConnectionError("the tool is unreachable")

    result = await run_station(broken, Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("did_not_run", False, None)
    assert result.reason == "the station raised ConnectionError: the tool is unreachable"
    assert result.output is None and result.decision is None


async def test_a_station_that_returns_the_wrong_type_did_not_run() -> None:
    @station
    async def wrong(ctx: StationContext, text: Text) -> Count:
        return text  # type: ignore[return-value]

    result = await run_station(wrong, Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran) == ("did_not_run", False)
    assert result.reason == "the station returned Text, not Count"


async def test_a_station_with_no_gate_ran_but_was_not_judged() -> None:
    @station
    async def ungated(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await run_station(ungated, Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("not_judged", True, None)
    assert result.output is not None and result.decision is None


async def test_missing_evidence_means_the_gate_did_not_run_and_nothing_passed() -> None:
    @gate(version="1", requires=[Note])
    def needs_a_note(output: Artifact[Count], evidence: Evidence) -> Verdict:
        return passed()

    @station(gate=needs_a_note)
    async def forgetful(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await run_station(forgetful, Text(body="hello"), store=MemoryStore())

    assert (result.outcome, result.ran, result.passed) == ("did_not_run", True, None)
    assert result.reason == "gate 'needs_a_note' was not evaluated: missing evidence: Note"
    assert result.output is not None and result.decision is None


async def test_a_gate_that_asks_for_a_person_leaves_passed_unanswered() -> None:
    @gate(version="1")
    def sign_off(output: Artifact[Count], evidence: Evidence) -> Verdict:
        return needs_approval("ready for sign-off")

    @station(gate=sign_off)
    async def drafted(ctx: StationContext, text: Text) -> Count:
        return Count(words=1)

    result = await run_station(drafted, Text(body="hello"), store=MemoryStore())

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

    result = await run_station(
        careful, given, store=store, run_id="r9", mission_id="m-1", methodologies=[stamp]
    )

    assert result.outcome == "passed"
    assert result.decision is not None and result.output is not None
    assert result.decision.reasons == ("counted by splitting on spaces",)
    assert result.decision.methodologies == (stamp,)
    (note_ref,) = result.evidence
    assert result.decision.judged == (result.output, note_ref)

    producer = Producer(station="careful", station_version="2", run_id="r9", attempt=1)
    for ref, payload_type in ((note_ref, Note), (result.output, Count)):
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
            input=ctx.input.payload,
        )
        return Count(words=0)

    earlier = evaluate_gate(under_ten_words, Artifact.create(Count(words=12)), Evidence())
    assert isinstance(earlier, GateDecision)

    result = await run_station(
        curious,
        Text(body="hi"),
        store=MemoryStore(),
        deps=Deps(greeting="hello"),
        attempt=2,
        feedback=earlier,
    )

    assert result.attempt == 2
    assert seen == {
        "greeting": "hello",
        "attempt": 2,
        "feedback": earlier,
        "input": Text(body="hi"),
    }


async def test_the_wrong_input_type_is_refused_before_anything_runs() -> None:
    store = MemoryStore()

    with pytest.raises(StationInputInvalid, match="'count_words' takes Text, not Count"):
        await run_station(count_words, Count(words=1), store=store)  # type: ignore[arg-type]
    assert len(store) == 0


def test_a_badly_declared_station_is_refused_with_the_reason() -> None:
    declare: Any = station  # typed loosely, because each of these is wrong on purpose

    async def unannotated(ctx, text):  # type: ignore[no-untyped-def]
        return Count(words=0)

    async def no_input(ctx: StationContext) -> Count:
        return Count(words=0)

    async def not_a_model(ctx: StationContext, text: str) -> Count:
        return Count(words=0)

    def synchronous(ctx: StationContext, text: Text) -> Count:
        return Count(words=0)

    async def unresolvable(ctx: StationContext, text: Missing) -> Count:  # type: ignore[name-defined]  # noqa: F821
        return Count(words=0)

    cases: list[tuple[Any, str]] = [
        (unannotated, "annotate the input and the return type"),
        (no_input, "must take a context and one input"),
        (not_a_model, "input type must be a Pydantic model"),
        (synchronous, "run must be an async function"),
        (unresolvable, "cannot resolve the annotation 'Missing'"),
    ]
    for function, message in cases:
        with pytest.raises(StationDefinitionError, match=message):
            declare(function)

    with pytest.raises(StationDefinitionError, match="the name must be non-empty"):
        Station(name=" ", run=count_words.run, input_type=Text, output_type=Count)
