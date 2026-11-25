"""A factory or one station as a system under test: questions go to the stakeholder port,
the factory's own approvals are given and logged, and the passing output is delivered.

Every model here is scripted. No real model is called.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from asking import ask
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_factory import Artifact, AskPerson, Factory, Station, station
from pydantic_ai_factory.evals import (
    Case,
    FactorySystem,
    FactSheet,
    RecordingModel,
    StationSystem,
    grade,
    run_case,
    scripted_picker,
)
from pydantic_ai_factory.gates import Evidence, Verdict, failed, gate

pytestmark = pytest.mark.anyio

SHEET = FactSheet.model_validate(
    {
        "case_name": "tasks",
        "challenge_class": "critical-ambiguity",
        "canary": "canary-1234abcd5678",
        "fallback_reply": "I don't have anything to add on that.",
        "approval_reply": "I can't sign anything off here.",
        "scored": False,
        "facts": [
            {
                "id": "retention",
                "value": "47 days",
                "criticality": "critical",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "Ticked tasks are kept for 47 days.",
                "direct_question": "How long are ticked tasks kept?",
                "keywords": ["kept"],
            }
        ],
    }
)
CASE = Case(name="tasks", request="Build a to-do page.")


class Plan(BaseModel):
    """What the station under test makes: a plan, in one line."""

    text: str


def answered(messages: list[ModelMessage]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == "ask_user_question"
    ]


def planner(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """Asks how long ticked tasks are kept, then writes the reply into its plan."""
    replies = answered(messages)
    if not replies:
        return ask("How long are ticked tasks kept?")
    plan = {"text": f"A to-do page. {replies[0]}"}
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, plan)])


def build_station(model: Model, workspace: AbstractCapability[Any] | None) -> Station[str, Plan]:
    agent = Agent(model, name="planner", output_type=Plan, capabilities=[AskPerson()])
    return station(agent, name="plan", approval=True)


async def run(system: Any, out: Path) -> Any:
    return await run_case(
        system,
        CASE,
        SHEET,
        arm=system.name,
        out=out,
        picker=scripted_picker(SHEET),
        cap_usd=0.5,
        time_limit_seconds=60.0,
    )


async def test_a_station_asks_through_the_port_is_approved_and_delivers_its_output(
    tmp_path: Path,
) -> None:
    system = StationSystem(name="plan", model=FunctionModel(planner), build=build_station)

    record = await run(system, tmp_path)

    assert record.end == "finished"
    assert record.delivery is not None and record.delivery.status == "delivered"
    assert "Ticked tasks are kept for 47 days." in record.delivery.text
    assert Plan.model_validate(record.delivery.output).text.startswith("A to-do page.")
    kinds = [event.kind for event in record.events]
    assert kinds.count("question_asked") == 1
    approvals = [e.data["what"] for e in record.events if e.kind == "adapter_approved"]
    assert approvals == ["approval"]
    assert grade(record, SHEET).outcome == "verified"


async def test_a_factory_system_shows_its_output_with_the_given_rendering(tmp_path: Path) -> None:
    def build(model: Model, workspace: AbstractCapability[Any] | None) -> Factory:
        return Factory("plans", [build_station(model, workspace)])

    system = FactorySystem(
        name="plans",
        model=FunctionModel(planner),
        build=build,
        render=lambda output: f"PLAN: {Plan.model_validate(output).text}",
    )

    record = await run(system, tmp_path)

    assert record.delivery is not None
    assert record.delivery.text.startswith("PLAN: A to-do page.")


async def test_a_station_that_keeps_failing_its_gate_is_delivered_as_blocked(
    tmp_path: Path,
) -> None:
    @gate(version="1")
    def never(output: Artifact[Plan], evidence: Evidence) -> Verdict:
        return failed("the plan is never good enough")

    def build(model: Model, workspace: AbstractCapability[Any] | None) -> Station[str, Plan]:
        agent = Agent(model, name="planner", output_type=Plan, capabilities=[AskPerson()])
        return station(agent, name="plan", gate=never)

    system = StationSystem(name="plan", model=FunctionModel(planner), build=build, repairs=1)

    record = await run(system, tmp_path)

    assert record.delivery is not None and record.delivery.status == "blocked"


async def test_recording_model_prices_nothing_it_cannot_and_counts_tokens() -> None:
    def text(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return ModelResponse(parts=[TextPart("Done.")])

    recording = RecordingModel(FunctionModel(text))
    await Agent(recording, name="writer").run("Write.")

    report = recording.report("finished")
    assert len(recording.responses) == 1
    assert report.cost_usd is None
    assert report.input_tokens > 0 and report.output_tokens > 0
