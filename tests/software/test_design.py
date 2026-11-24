"""The software factory's Design station: draft, review by fresh goldfish, then a person."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import pytest
from pydantic_ai import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.profiles import ModelProfile

import pydantic_ai_factory_software as software_package
from pydantic_ai_factory import Factory, FactoryDefinitionError, MemoryStore, Mission
from pydantic_ai_factory.methodologies import Goldfish, GoldfishReport
from pydantic_ai_factory_software import (
    DESIGN_GOLDFISH,
    DESIGN_STANDARDS,
    Design,
    SoftwareFactory,
    render_markdown,
)
from pydantic_ai_factory_software._design import DESIGNER_INSTRUCTIONS

MISSION = "A to-do list web page where I can add tasks and tick them off."
TODO: dict[str, Any] = {
    "title": "To-do list",
    "goal": "Keep track of tasks in a browser.",
    "behaviour": ["Type a task and press Enter to add it.", "Tick a task's box to mark it done."],
    "acceptance_criteria": [
        "A task typed and entered appears in the list.",
        "Ticked tasks stay ticked after a reload.",
    ],
    "exclusions": ["Accounts"],
}
BLANK = "Pressing Enter on a blank task adds nothing."
BLANK_FINDING = {
    "severity": "Major",
    "issue": "The design never says what happens to a blank task.",
}


def said(messages: list[ModelMessage]) -> str:
    return "\n".join(
        str(getattr(part, "content", ""))
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
    )


def output(info: AgentInfo, args: dict[str, Any]) -> ModelResponse:
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])


class Designer:
    """The designer's model: gives drafts in turn, and keeps every conversation it saw."""

    def __init__(self, *drafts: dict[str, Any] | ModelResponse) -> None:
        self.drafts = list(drafts)
        self.seen: list[list[ModelMessage]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.seen.append(list(messages))
        reply = self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
        return reply if isinstance(reply, ModelResponse) else output(info, reply)


class Pond:
    """The goldfish's model: each goldfish, told apart by its stage, gives findings in turn."""

    def __init__(self, **findings: list[list[dict[str, Any]]]) -> None:
        self.findings = findings
        self.read: dict[str, list[str]] = {fish.name: [] for fish in DESIGN_GOLDFISH}

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = next(
            fish.name
            for fish in DESIGN_GOLDFISH
            if f"Stage: {fish.stage}." in (info.instructions or "")
        )
        self.read[name].append(said(messages[:1]))
        queue = self.findings.get(name, [[]])
        found = queue.pop(0) if len(queue) > 1 else queue[0]
        reply: dict[str, Any] = {"findings": found}
        if name == "comprehension":
            reply["paraphrase"] = "A to-do list in the browser: add tasks, tick them off."
        return output(info, reply)


def factory(designer: Designer, pond: Pond | None = None, **options: Any) -> SoftwareFactory:
    return SoftwareFactory(
        FunctionModel(designer), review_model=FunctionModel(pond or Pond()), **options
    )


def reports(store: MemoryStore, refs: Any) -> list[GoldfishReport]:
    return [
        store.get_sync(ref, GoldfishReport).payload for ref in refs if ref.type == "GoldfishReport"
    ]


def test_a_mission_is_drafted_reviewed_and_waits_for_a_person_to_approve_the_design() -> None:
    pond = Pond()
    software = factory(Designer({**TODO, "behaviour": [*TODO["behaviour"], BLANK]}), pond)
    store = MemoryStore()

    run = software.run_sync(MISSION, store=store)

    assert run.outcome == "waiting"
    assert run.waiting is not None and run.waiting.kind == "approval"
    assert isinstance(run.waiting.output, Design)
    assert run.waiting.output.title == "To-do list"
    assert run.mission_ref is not None
    assert {name: len(texts) for name, texts in pond.read.items()} == {
        "comprehension": 1,
        "critique": 1,
        "readiness": 1,
    }
    assert "Ticked tasks stay ticked after a reload." in pond.read["readiness"][0]


def test_approving_the_design_passes_the_run_with_the_design_as_its_output() -> None:
    software = factory(Designer({**TODO, "behaviour": [*TODO["behaviour"], BLANK]}))
    store = MemoryStore()
    run = software.run_sync(MISSION, store=store)

    done = software.resume_sync(run.approve(by="ada"), store=store)

    assert done.outcome == "passed"
    assert isinstance(done.output, Design)
    assert done.output.behaviour[-1] == BLANK
    assert [decision.by for decision in done.answers] == ["ada"]


def test_a_finding_goes_back_to_the_designer_who_revises_in_the_same_conversation() -> None:
    designer = Designer(TODO, {**TODO, "behaviour": [*TODO["behaviour"], BLANK]})
    software = factory(designer, Pond(critique=[[BLANK_FINDING], []]))
    store = MemoryStore()

    run = software.run_sync(MISSION, store=store)

    assert run.waiting is not None and run.waiting.kind == "approval"
    rounds = reports(store, run.steps[-1].evidence)
    assert [(f.id, f.severity, f.issue) for f in rounds[0].findings] == [
        ("r1-critique-1", "Major", "The design never says what happens to a blank task.")
    ]
    assert rounds[-1].clean and rounds[-1].round == 2
    assert MISSION in said(designer.seen[-1])
    assert "r1-critique-1" in said(designer.seen[-1])


def test_a_contested_finding_goes_to_a_person_who_can_accept_the_design_once() -> None:
    contest = ModelResponse(
        parts=[
            ToolCallPart(
                "contest",
                {"finding_id": "r1-readiness-1", "evidence": "The mission asks for one file."},
                "contest-1",
            )
        ]
    )
    one_file = {**TODO, "behaviour": [*TODO["behaviour"], BLANK, "Everything is in index.html."]}
    styles = {"severity": "Major", "issue": "The design does not say where the styles go."}
    software = factory(Designer(one_file, contest, one_file), Pond(readiness=[[styles], []]))
    store = MemoryStore()

    run = software.run_sync("A to-do list page. Keep it to one file.", store=store)

    assert run.waiting is not None and run.waiting.kind == "escalation"
    assert run.waiting.reasons == (
        "clean_final_round: r1-readiness-1 contested: The design does not say where the "
        "styles go. (The mission asks for one file.)",
    )
    done = software.resume_sync(run.approve(by="ada", note="One file is right."), store=store)
    assert done.outcome == "passed"
    assert [a.model_dump(include={"waiting", "overrode"}) for a in done.answers] == [
        {"waiting": "escalation", "overrode": True}
    ]


def test_asking_for_changes_sends_the_note_to_the_designer_and_reviews_afresh() -> None:
    first = {**TODO, "behaviour": [*TODO["behaviour"], BLANK]}
    deleting = {**first, "behaviour": [*first["behaviour"], "Each task has a delete button."]}
    designer = Designer(first, deleting)
    pond = Pond()
    software = factory(designer, pond)
    store = MemoryStore()
    run = software.run_sync(MISSION, store=store)

    run = software.resume_sync(
        run.request_changes(by="ada", note="Add a way to delete tasks."), store=store
    )

    assert run.waiting is not None and run.waiting.kind == "approval"
    assert isinstance(run.waiting.output, Design)
    assert run.waiting.output.behaviour[-1] == "Each task has a delete button."
    assert "ada: Add a way to delete tasks." in said(designer.seen[-1])
    assert MISSION in said(designer.seen[-1])
    assert [r.round for r in reports(store, run.steps[-1].evidence)] == [1]
    assert "delete button" in pond.read["critique"][-1]


def test_a_design_with_open_questions_is_sent_back_then_a_person_decides() -> None:
    asking = {**TODO, "open_questions": ["Should ticked tasks move to the bottom?"]}
    designer = Designer(asking)
    software = factory(designer, repairs=2)
    store = MemoryStore()

    run = software.run_sync(MISSION, store=store)

    assert run.attempts("design") == 3
    assert run.reworked == {"design": 2}
    assert run.waiting is not None and run.waiting.kind == "escalation"
    assert run.waiting.reasons[0] == "rework ran out"
    assert "no_open_questions: open question: Should ticked tasks move to the bottom?" in (
        run.waiting.reasons
    )
    assert "Should ticked tasks move to the bottom?" in said(designer.seen[-1])


def test_a_design_that_settles_its_open_questions_goes_on_to_approval() -> None:
    asking = {**TODO, "open_questions": ["Should ticked tasks move to the bottom?"]}
    software = factory(Designer(asking, TODO))
    store = MemoryStore()

    run = software.run_sync(MISSION, store=store)

    assert run.waiting is not None and run.waiting.kind == "approval"
    assert run.reworked == {"design": 1}


def test_a_mission_with_criteria_and_constraints_is_given_to_the_designer_whole() -> None:
    designer = Designer(TODO)
    mission = Mission(MISSION, success_criteria=["Works offline."], constraints=["One file."])

    run = factory(designer).run_sync(mission, store=MemoryStore())

    assert run.mission_id == mission.id
    assert "- Works offline." in said(designer.seen[0])
    assert "- One file." in said(designer.seen[0])


def test_the_goldfish_use_the_designers_model_unless_given_their_own() -> None:
    pond = Pond()
    designer = Designer(TODO)

    def both(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if "goldfish" in (info.instructions or ""):
            return pond(messages, info)
        return designer(messages, info)

    run = SoftwareFactory(FunctionModel(both)).run_sync(MISSION, store=MemoryStore())

    assert run.waiting is not None and run.waiting.kind == "approval"
    assert len(designer.seen) == 1
    assert len(pond.read["readiness"]) == 1


def test_the_factory_names_its_station_agent_and_methodology_before_a_run() -> None:
    software = SoftwareFactory(FunctionModel(Designer(TODO)))

    assert isinstance(software, Factory)
    assert software.name == "software"
    assert [station.name for station in software.stations] == ["design"]
    assert software.stations[0].approval
    assert [agent.name for agent in software.agents()["design"]] == ["designer"]
    assert [str(m.ref) for m in software.methodologies()["design"]] == ["elephant-goldfish@1"]
    assert (software.rounds, software.repairs) == (2, 2)
    assert software.goldfish == DESIGN_GOLDFISH


def test_the_factory_cannot_be_changed_once_made() -> None:
    software = SoftwareFactory(FunctionModel(Designer(TODO)))

    with pytest.raises(FrozenInstanceError):
        software.rounds = 3  # pyright: ignore[reportAttributeAccessIssue]


def test_options_reach_the_review_and_the_rework_rule() -> None:
    fish = (Goldfish("only", stage="critique", job="Find what is wrong."),)
    software = SoftwareFactory(FunctionModel(Designer(TODO)), rounds=4, repairs=1, goldfish=fish)

    review = software.methodologies()["design"][0]
    assert getattr(review, "rounds") == 4  # noqa: B009
    assert [f.name for f in getattr(review, "goldfish")] == ["only"]  # noqa: B009
    assert [(rule.limit, rule.on_limit) for rule in software.rework] == [(1, "escalate")]


def test_a_software_factory_needs_a_model() -> None:
    with pytest.raises(FactoryDefinitionError, match="give it a model"):
        SoftwareFactory(None)  # pyright: ignore[reportArgumentType]


def test_a_software_factory_refuses_a_repairs_limit_below_one() -> None:
    with pytest.raises(FactoryDefinitionError, match="the limit must be a whole number"):
        SoftwareFactory(FunctionModel(Designer(TODO)), repairs=0)


def test_the_design_goldfish_read_in_order_each_held_to_its_own_standard() -> None:
    assert [(f.name, f.stage) for f in DESIGN_GOLDFISH] == [
        ("comprehension", "comprehension"),
        ("critique", "critique"),
        ("readiness", "readiness"),
    ]
    assert tuple(f.standard for f in DESIGN_GOLDFISH) == DESIGN_STANDARDS


def test_render_markdown_numbers_the_criteria_and_says_when_a_section_is_empty() -> None:
    design = Design.model_validate(TODO)

    text = render_markdown(design)

    assert text.startswith("# To-do list\n\nKeep track of tasks in a browser.\n")
    assert "- **AC2** Ticked tasks stay ticked after a reload." in text
    assert "## Open questions\n\nNone.\n" in text
    assert render_markdown(Design.model_validate(TODO)) == text


def test_a_design_needs_behaviour_and_acceptance_criteria() -> None:
    with pytest.raises(ValueError, match="acceptance_criteria"):
        Design.model_validate({**TODO, "acceptance_criteria": []})


def test_the_package_exports_only_the_factory_its_design_parts_and_its_scripted_model() -> None:
    assert sorted(software_package.__all__) == sorted(
        [
            "DESIGN_GOLDFISH",
            "DESIGN_STANDARDS",
            "Design",
            "SoftwareFactory",
            "__version__",
            "offline_model",
            "render_markdown",
        ]
    )


def test_render_markdown_does_not_repeat_a_label_the_designer_wrote() -> None:
    design = Design.model_validate(
        {
            **TODO,
            "acceptance_criteria": [
                "AC1: A task typed and entered appears in the list.",
                "ac2 - Ticked tasks stay ticked after a reload.",
                "AC3 is not a label when no separator follows the number.",
            ],
        }
    )

    text = render_markdown(design)

    assert "- **AC1** A task typed and entered appears in the list." in text
    assert "- **AC2** Ticked tasks stay ticked after a reload." in text
    assert "- **AC3** AC3 is not a label when no separator follows the number." in text


def test_the_designer_is_told_not_to_repeat_a_contested_finding_as_an_open_question() -> None:
    assert "do not also list it as an open question" in DESIGNER_INSTRUCTIONS
    description = Design.model_fields["acceptance_criteria"].description or ""
    assert "do not number them" in description.lower()


def test_the_designer_and_every_goldfish_ask_for_prompt_caching() -> None:
    cached: list[object] = []

    def recording(model: Designer | Pond) -> FunctionModel:
        def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            cached.append(info.model_request_parameters.cache)
            return model(messages, info)

        # A scripted model ignores caching unless its profile says it supports it.
        return FunctionModel(respond, profile=ModelProfile(supports_cache=True))

    designer = Designer({**TODO, "behaviour": [*TODO["behaviour"], BLANK]})
    software = SoftwareFactory(recording(designer), review_model=recording(Pond()))

    software.run_sync(MISSION, store=MemoryStore())

    assert cached == [True] * (1 + len(DESIGN_GOLDFISH))
