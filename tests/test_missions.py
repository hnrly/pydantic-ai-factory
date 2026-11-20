"""A mission is what a factory commits to; signals are how work can arrive on its own."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_factory import (
    Declined,
    Factory,
    FactoryDefinitionError,
    IntakeError,
    MemoryStore,
    Mission,
    Outcome,
    Signal,
    StationContext,
    StationInputInvalid,
    station,
)

pytestmark = pytest.mark.anyio


class BugReport(BaseModel):
    title: str


@station
async def design(ctx: StationContext[Any], mission: Mission) -> str:
    return f"A design for: {mission.objective}"


@station(approval=True)
async def build(ctx: StationContext[Any], design: str) -> str:
    return f"Built from {design}"


async def triage(signals: Sequence[Signal[Any]]) -> Mission | Declined:
    bugs = [s.payload for s in signals if isinstance(s.payload, BugReport)]
    if not bugs:
        return Declined("no bug report among the signals")
    return Mission(f"Fix the to-do list page: {bugs[0].title}")


software = Factory("software", [design], accept=triage)
REPORT = BugReport(title="Ticked tasks come back after a reload")


def test_a_mission_is_a_frozen_record_with_an_id() -> None:
    mission = Mission(
        "A to-do list web page.",
        success_criteria=["Ticked tasks stay ticked after a reload."],
        constraints=("One HTML file, no server.",),
    )

    assert mission.id.startswith("mission-")
    assert mission.success_criteria == ("Ticked tasks stay ticked after a reload.",)
    assert mission.signals == ()
    assert Mission.model_validate_json(mission.model_dump_json()) == mission
    assert Mission("x").id != Mission("x").id
    with pytest.raises(ValueError, match="frozen"):
        mission.objective = "something else"  # type: ignore[misc]


async def test_a_string_becomes_a_mission_when_the_first_station_takes_one() -> None:
    store = MemoryStore()

    run = await software.run("A to-do list web page.", store=store)

    first = await store.get(run.input_ref, Mission)
    assert first.payload.objective == "A to-do list web page."
    assert run.mission_id == first.payload.id
    assert run.output == "A design for: A to-do list web page."


async def test_every_artifact_a_run_makes_carries_the_missions_id() -> None:
    store = MemoryStore()
    mission = Mission("A to-do list web page.")

    run = await Factory("software", [design, build]).run(mission, store=store)
    run = await Factory("software", [design, build]).resume(run.approve(by="luke"), store=store)

    refs = [run.input_ref, *(step.output_ref for step in run.steps if step.output_ref)]
    assert len(refs) == 3
    for ref in refs:
        assert (await store.get(ref, Any)).mission_id == mission.id


def echo_prompt(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    prompt = next(
        part.content
        for message in messages
        for part in getattr(message, "parts", ())
        if isinstance(part, UserPromptPart)
    )
    return ModelResponse(parts=[TextPart(f"Design for: {prompt}")])


designer = Agent(FunctionModel(echo_prompt), name="designer")


def test_a_mission_with_only_an_objective_is_its_own_prompt() -> None:
    assert Mission("A to-do list web page.").as_prompt() == "A to-do list web page."


def test_a_missions_prompt_lists_its_criteria_and_constraints_when_it_has_them() -> None:
    mission = Mission(
        "A to-do list web page.",
        success_criteria=("Ticked tasks stay ticked after a reload.",),
        constraints=("One HTML file.", "No server."),
    )

    assert mission.as_prompt() == (
        "A to-do list web page.\n\n"
        "Success criteria:\n- Ticked tasks stay ticked after a reload.\n\n"
        "Constraints:\n- One HTML file.\n- No server."
    )
    assert Mission("x", constraints=("y",)).as_prompt() == "x\n\nConstraints:\n- y"


async def test_a_station_that_takes_text_is_given_the_mission_as_its_prompt() -> None:
    store = MemoryStore()
    factory = Factory("software", [station(designer)])
    mission = Mission("A to-do list web page.", constraints=("One HTML file.",))

    run = await factory.run(mission, store=store)

    assert run.output == f"Design for: {mission.as_prompt()}"
    assert run.mission_id == mission.id
    assert run.mission_ref is not None
    assert (await store.get(run.mission_ref, Mission)).payload == mission
    first = await store.get(run.input_ref, str)
    assert (first.inputs, first.mission_id) == ((run.mission_ref,), mission.id)
    for step in run.steps:
        assert step.output_ref is not None
        assert (await store.get(step.output_ref, Any)).mission_id == mission.id


async def test_a_string_and_a_mission_of_it_give_a_text_station_the_same_prompt() -> None:
    factory = Factory("software", [station(designer)])

    from_text = await factory.run("A to-do list web page.", store=MemoryStore())
    from_mission = await factory.run(Mission("A to-do list web page."), store=MemoryStore())

    assert from_text.output == from_mission.output == "Design for: A to-do list web page."
    assert from_text.mission_id is not None
    assert from_text.mission_id != from_mission.mission_id


async def test_a_mission_made_by_intake_runs_through_an_agent_station_and_emits_its_outcome() -> (
    None
):
    store = MemoryStore()
    factory = Factory("software", [station(designer)], accept=triage)
    mission = await factory.intake([Signal(REPORT)], store=store)
    assert isinstance(mission, Mission)

    run = await factory.run(mission, store=store)

    assert run.output == f"Design for: {mission.objective}"
    assert run.outcome_signal is not None
    outcome = (await store.get(run.outcome_signal, Signal[Outcome])).payload.payload
    assert (outcome.mission_id, outcome.mission_ref, outcome.outcome) == (
        mission.id,
        run.mission_ref,
        "passed",
    )
    assert (await store.get(outcome.mission_ref, Mission)).inputs == mission.signals


async def test_a_factory_whose_first_station_takes_something_else_runs_without_a_mission() -> None:
    @station
    async def count(ctx: StationContext[Any], words: list[str]) -> int:
        return len(words)

    run = await Factory("count", [count]).run(["a", "b"], store=MemoryStore())

    assert (run.output, run.mission_id, run.mission_ref) == (2, None, None)


async def test_a_blank_objective_is_refused_before_any_station_runs() -> None:
    with pytest.raises(StationInputInvalid, match="at least 1 character"):
        await software.run("", store=MemoryStore())


async def test_a_mission_built_by_hand_records_no_signals_and_emits_nothing() -> None:
    store = MemoryStore()

    run = await software.run(Mission("A to-do list web page."), store=store)

    assert (run.outcome, run.outcome_signal) == ("passed", None)
    assert len(store) == 2  # the mission and the design, nothing more


async def test_intake_stores_each_signal_and_the_mission_records_them() -> None:
    store = MemoryStore()
    report = Signal(REPORT, source="github")

    mission = await software.intake([report], store=store)

    assert isinstance(mission, Mission)
    assert mission.objective == "Fix the to-do list page: Ticked tasks come back after a reload"
    (ref,) = mission.signals
    stored = await store.get(ref, Signal[BugReport])
    assert (stored.payload.payload, stored.payload.source) == (REPORT, "github")


async def test_intake_does_not_start_a_run() -> None:
    store = MemoryStore()

    await software.intake([Signal(REPORT)], store=store)

    assert len(store) == 1


async def test_a_declined_intake_names_the_signals_it_turned_down() -> None:
    store = MemoryStore()

    declined = await software.intake([Signal("a chat message")], store=store)

    assert isinstance(declined, Declined)
    assert declined.reason == "no bug report among the signals"
    assert len(declined.signals) == 1
    assert await store.exists(declined.signals[0])


async def test_the_signals_stay_stored_when_accept_raises() -> None:
    store = MemoryStore()

    def broken(signals: Sequence[Signal[Any]]) -> Mission:
        raise RuntimeError("the triage service is down")

    with pytest.raises(RuntimeError, match="down"):
        await Factory("software", [design], accept=broken).intake([Signal(REPORT)], store=store)
    assert len(store) == 1


def test_accept_may_be_a_plain_function_and_intake_may_be_called_from_sync_code() -> None:
    def take_everything(signals: Sequence[Signal[Any]]) -> Mission:
        return Mission(f"{len(signals)} things")

    factory = Factory("software", [design], accept=take_everything)
    mission = factory.intake_sync([Signal(REPORT), Signal("x")], store=MemoryStore())

    assert isinstance(mission, Mission)
    assert (mission.objective, len(mission.signals)) == ("2 things", 2)


async def test_intake_refuses_what_it_cannot_decide() -> None:
    store = MemoryStore()

    def confused(signals: Sequence[Signal[Any]]) -> Any:
        return "yes"

    with pytest.raises(IntakeError, match="no accept function"):
        await Factory("software", [design]).intake([Signal(REPORT)], store=store)
    with pytest.raises(IntakeError, match="at least one signal"):
        await software.intake([], store=store)
    with pytest.raises(IntakeError, match="wrap it in Signal"):
        await software.intake([REPORT], store=store)  # type: ignore[list-item]
    with pytest.raises(IntakeError, match="returned str"):
        await Factory("software", [design], accept=confused).intake([Signal(REPORT)], store=store)


def test_a_factory_refuses_an_accept_that_is_not_a_function() -> None:
    with pytest.raises(FactoryDefinitionError, match="accept must be a function"):
        Factory("software", [design], accept="triage")  # type: ignore[arg-type]


def test_storing_the_same_signal_twice_gives_the_same_artifact() -> None:
    store = MemoryStore()
    report = Signal(REPORT)

    first = software.intake_sync([report], store=store)
    second = software.intake_sync([report], store=store)

    assert isinstance(first, Mission) and isinstance(second, Mission)
    assert first.signals == second.signals
    assert first.id != second.id


async def test_a_run_of_a_mission_made_by_intake_emits_an_outcome_signal_when_it_ends() -> None:
    store = MemoryStore()
    mission = await software.intake([Signal(REPORT)], store=store)
    assert isinstance(mission, Mission)

    run = await software.run(mission, store=store)

    assert run.outcome_signal is not None
    assert (await store.get_run(run.run_id)).outcome_signal == run.outcome_signal
    emitted = await store.get(run.outcome_signal, Signal[Outcome])
    outcome = emitted.payload.payload
    assert emitted.payload.source == "software"
    assert (outcome.factory, outcome.run_id, outcome.mission_id) == (
        "software",
        run.run_id,
        mission.id,
    )
    assert (outcome.outcome, outcome.finished, outcome.output_ref) == (
        "passed",
        True,
        run.output_ref,
    )
    assert outcome.mission_ref == run.mission_ref == run.input_ref
    assert (await store.get(run.input_ref, Mission)).inputs == mission.signals


async def test_a_waiting_run_emits_its_outcome_only_once_it_ends() -> None:
    store = MemoryStore()
    factory = Factory("software", [design, build], accept=triage)
    mission = await factory.intake([Signal(REPORT)], store=store)

    waiting = await factory.run(mission, store=store)
    assert (waiting.outcome, waiting.outcome_signal) == ("waiting", None)

    ended = await factory.resume(waiting.reject(by="luke", note="not this way"), store=store)
    assert ended.outcome_signal is not None
    outcome = (await store.get(ended.outcome_signal, Signal[Outcome])).payload.payload
    assert (outcome.outcome, outcome.finished, outcome.output_ref) == ("failed", False, None)


async def test_an_outcome_signal_can_come_back_in_through_intake() -> None:
    store = MemoryStore()
    mission = await software.intake([Signal(REPORT)], store=store)
    run = await software.run(mission, store=store)
    assert run.outcome_signal is not None
    seen: list[object] = []

    def follow_up(signals: Sequence[Signal[Any]]) -> Declined:
        seen.extend(s.payload for s in signals)
        return Declined("nothing more to do")

    emitted = await store.get(run.outcome_signal, Signal[Outcome])
    declined = await Factory("software", [design], accept=follow_up).intake(
        [emitted.payload], store=store
    )

    assert isinstance(declined, Declined)
    assert declined.signals == (run.outcome_signal,)
    assert isinstance(seen[0], Outcome)
