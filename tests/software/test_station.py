"""The Design station composed from skills: its goldfish, its designer's skills and its API."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import Agent, ModelMessage, ModelRequest, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.workspaces import local
from pydantic_ai_harness.skills import Skills
from pydantic_ai_harness.skills._loader import parse_skill
from scripted import PLAN

from pydantic_ai_factory import Factory, FactoryDefinitionError, MemoryStore, Rework
from pydantic_ai_factory.methodologies import (
    ElephantGoldfish,
    Goldfish,
    GoldfishReport,
    PackageFolder,
)
from pydantic_ai_factory_software import (
    DESIGN_GOLDFISH,
    DESIGN_STANDARDS,
    Design,
    SoftwareFactory,
    design_is_buildable,
    design_review,
    design_station,
    render_markdown,
)
from pydantic_ai_factory_software._station import READINESS, SKILLS

MISSION = "A to-do list web page where I can add tasks and tick them off."
BLANK = "Pressing Enter on a blank task adds nothing."
TODO: dict[str, Any] = {
    "title": "To-do list",
    "summary": "Keep track of tasks in a browser.",
    "behaviour": ["Type a task and press Enter to add it.", BLANK],
    "acceptance_criteria": [
        "A task typed and entered appears in the list.",
        "Ticked tasks stay ticked after a reload.",
    ],
    "non_goals": ["Accounts"],
    **PLAN,
}
TEXT = render_markdown(Design.model_validate(TODO))
EXPLAIN_BACK = {"problem": "Keeping track of tasks.", "who": "One person.", "approach": "A page."}
FOUR_SKILLS = ["design-doc", "design-interview", "design-read-code", "design-resolve"]


def finding(kind: str, severity: str = "Nit", quote: str = BLANK) -> dict[str, Any]:
    return {"severity": severity, "kind": kind, "quote": quote, "issue": f"A {kind} finding."}


class Designer:
    """The designer's model: drafts in turn, and keeps what it was offered and sent."""

    def __init__(self, *drafts: dict[str, Any] | ModelResponse) -> None:
        self.drafts = list(drafts or [TODO])
        self.instructions: list[str] = []
        self.tools: list[list[str]] = []
        self.seen: list[list[ModelMessage]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.instructions.append(info.instructions or "")
        self.tools.append(sorted(tool.name for tool in info.function_tools))
        self.seen.append(list(messages))
        reply = self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0]
        if isinstance(reply, ModelResponse):
            return reply
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


class Pond:
    """The goldfish's model: each goldfish, told apart by its stage, replies in turn."""

    def __init__(self, **replies: list[list[dict[str, Any]]]) -> None:
        self.replies = replies
        self.prompts: dict[str, list[str]] = {fish.name: [] for fish in DESIGN_GOLDFISH}
        self.instructions: dict[str, str] = {}

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        name = next(
            fish.name
            for fish in DESIGN_GOLDFISH
            if f"Stage: {fish.stage}." in (info.instructions or "")
        )
        first = messages[0]
        assert isinstance(first, ModelRequest)
        self.prompts[name].append(str(getattr(first.parts[-1], "content", "")))
        self.instructions[name] = info.instructions or ""
        queue = self.replies.get(name, [[]])
        found = queue.pop(0) if len(queue) > 1 else queue[0]
        reply: dict[str, Any] = {"findings": found}
        if name == "comprehension":
            reply["explain_back"] = EXPLAIN_BACK
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, reply)])


def reviewed(designer: Designer, pond: Pond) -> Any:
    """Run the Design station as shipped, with scripted models, and return its result."""
    review = design_review(FunctionModel(pond))
    design = design_station(FunctionModel(designer), method=review)
    store = MemoryStore()
    result = design.run_sync(MISSION, store=store)
    reports = [
        store.get_sync(ref, GoldfishReport).payload
        for ref in result.evidence
        if ref.type == "GoldfishReport"
    ]
    return result, reports


def catalogue(instructions: str) -> list[str]:
    """The skills the designer was offered to load, by name."""
    return sorted(re.findall(r"^- ([a-z0-9-]+): ", instructions, re.MULTILINE))


# The goldfish.


def test_a_finding_of_every_kind_lands_in_a_report_with_its_quote_checked() -> None:
    kinds = {fish.name: fish.kinds for fish in DESIGN_GOLDFISH}
    pond = Pond(
        **{name: [[finding(kind) for kind in listed], []] for name, listed in kinds.items()}
    )

    result, reports = reviewed(Designer(), pond)

    assert result.outcome == "waiting"  # for a person to approve the design
    first = reports[0]
    assert {
        name: tuple(f.kind for f in first.findings if f.goldfish == name) for name in kinds
    } == (kinds)
    assert len(first.findings) == 1 + 7 + 7
    # Every quote was checked against the Markdown the goldfish read, and kept.
    assert all(f.quote == BLANK and f.quote in TEXT for f in first.findings)
    assert all("quote not found" not in f.issue for f in first.findings)


def test_comprehension_never_sees_the_request() -> None:
    pond = Pond()

    reviewed(Designer(), pond)

    assert MISSION not in pond.prompts["comprehension"][0]
    assert "<request-" not in pond.prompts["comprehension"][0]
    assert MISSION in pond.prompts["critique"][0]
    assert MISSION in pond.prompts["readiness"][0]
    # Every goldfish reads the design as Markdown, not as JSON.
    assert all(TEXT in prompts[0] for prompts in pond.prompts.values())


def test_contradicts_code_is_a_blocker() -> None:
    contradicts = finding("contradicts_code", severity="Blocker")
    pond = Pond(readiness=[[contradicts], []])
    designer = Designer()

    _, reports = reviewed(designer, pond)

    assert "`contradicts_code`" in READINESS.job
    assert "always a Blocker" in pond.instructions["readiness"]
    [blocker] = reports[0].findings
    assert (blocker.kind, blocker.severity, blocker.blocking) == (
        "contradicts_code",
        "Blocker",
        True,
    )
    assert not reports[0].clean
    assert "r1-readiness-1, Blocker" in " ".join(
        str(getattr(part, "content", "")) for message in designer.seen[-1] for part in message.parts
    )


def test_critique_reports_at_most_seven() -> None:
    eight = [finding("gap") for _ in range(8)]
    seven = eight[:7]
    pond = Pond(critique=[eight, seven, []])

    _, reports = reviewed(Designer(), pond)

    [critique] = [fish for fish in DESIGN_GOLDFISH if fish.name == "critique"]
    assert critique.max_findings == 7
    assert "at most seven findings" in critique.job
    # Eight were sent back once; the seven that came next were kept.
    assert len(pond.prompts["critique"]) == 1 + 2
    assert len(reports[0].findings) == 7


def test_design_review_needs_two_clean_rounds() -> None:
    review = design_review()
    designer = Designer()

    _, reports = reviewed(designer, Pond())

    assert (review.rounds, review.clean_rounds, review.settle_contests) == (4, 2, True)
    assert review.to_text is render_markdown
    assert tuple(review.goldfish) == DESIGN_GOLDFISH
    assert [(r.round, r.clean) for r in reports] == [(1, True), (2, True)]
    assert len(designer.seen) == 1


def test_every_software_skill_parses_and_names_its_sources() -> None:
    names = sorted(folder.name for folder in SKILLS.iterdir() if (folder / "SKILL.md").is_file())

    assert names == ["design-doc", "design-read-code", "goldfish-readiness"]
    for name in names:
        skill = parse_skill(
            (SKILLS / name / "SKILL.md").read_text(encoding="utf-8"), name + "/SKILL.md"
        )
        assert skill.name == name
        assert 0 < len(skill.description) <= 1024
        assert "Google" in skill.body.split("## Sources", 1)[1], name


def test_readiness_is_built_from_its_skill() -> None:
    assert Goldfish.from_skill(SKILLS / "goldfish-readiness", sees_request=True) == READINESS
    assert (READINESS.name, READINESS.stage, READINESS.sees_request) == (
        "readiness",
        "readiness",
        True,
    )
    assert READINESS.kinds == (
        "missing_path",
        "contradicts_code",
        "nonexistent_reference",
        "ambiguous_interface",
        "decision_left_to_builder",
        "no_oracle",
        "order_conflict",
    )
    assert READINESS.job.startswith("You are the engineer who will build this design.")
    assert [standard.name for standard in DESIGN_STANDARDS] == ["clear", "faithful", "buildable"]
    assert DESIGN_STANDARDS[2].text.startswith("The Zero-Decision Rule.")


# The designer's skills.


def test_the_designer_is_offered_exactly_its_four_skills() -> None:
    designer = Designer()

    reviewed(designer, Pond())

    assert catalogue(designer.instructions[0]) == FOUR_SKILLS
    assert "Load design-read-code before you ask anything" in designer.instructions[0]
    assert "Load design-interview before you ask a person" in designer.instructions[0]


def test_the_software_factory_runs_where_a_local_workspace_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pydantic AI's local workspace refuses to start on Windows; the station never needs one."""

    def refuse(self: object, *args: object, **kwargs: object) -> None:
        raise NotImplementedError("LocalWorkspaceBackend only supports POSIX platforms")

    monkeypatch.setattr(local.LocalWorkspaceBackend, "__init__", refuse)
    designer = Designer()

    software = SoftwareFactory(FunctionModel(designer))
    result, _ = reviewed(designer, Pond())

    assert software.stations[0].name == "design"
    assert result.outcome == "waiting"
    assert catalogue(designer.instructions[0]) == FOUR_SKILLS


def test_without_review_the_designer_is_offered_the_same_four_skills() -> None:
    designer = Designer()
    plain = design_station(FunctionModel(designer), method=ElephantGoldfish(goldfish=()))

    result = plain.run_sync(MISSION, store=MemoryStore())

    assert result.outcome == "waiting"
    assert catalogue(designer.instructions[0]) == FOUR_SKILLS
    assert designer.tools[0] == ["ask_user_question", "load_capability"]


def test_a_method_skill_survives_a_later_catalogue_with_its_name(tmp_path: Path) -> None:
    """The open question on two catalogues: they list together, and the earlier one wins.

    Two ``Skills`` on one agent combine into one catalogue, including the method's own,
    which Elephant Goldfish holds as a child. ``duplicate_names='keep_first'`` on the later
    catalogue keeps the method's skill. Attached the other way round, the run fails.
    """
    for name, text in (("design-interview", "Ask everything."), ("extra-skill", "More.")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A later {name}.\n---\n\n{text}\n", encoding="utf-8"
        )
    later = Skills(".", duplicate_names="keep_first", workspace=PackageFolder(tmp_path))
    designer = Designer(ModelResponse(parts=[TextPart("Done.")]))
    agent = Agent(
        FunctionModel(designer),
        name="designer",
        capabilities=[ElephantGoldfish(goldfish=()), later],
    )

    with pytest.warns(UserWarning, match="'design-interview' is already taken"):
        agent.run_sync(MISSION)

    assert catalogue(designer.instructions[0]) == [
        "design-interview",
        "design-resolve",
        "extra-skill",
    ]
    assert "A later design-interview" not in designer.instructions[0]
    wrong_way = Agent(
        FunctionModel(Designer(ModelResponse(parts=[TextPart("Done.")]))),
        name="designer",
        capabilities=[
            Skills(".", workspace=PackageFolder(tmp_path)),
            ElephantGoldfish(goldfish=()),
        ],
    )
    with pytest.raises(ValueError, match="Duplicate skill name 'design-interview'"):
        wrong_way.run_sync(MISSION)


# The station's API.


def test_design_station_takes_a_method() -> None:
    only = Goldfish("only", stage="critique", job="Find what is wrong.")
    method = ElephantGoldfish(goldfish=(only,), model=FunctionModel(Pond()))

    design = design_station(FunctionModel(Designer()), method=method)

    # The designer's own AskPerson wins, so the attached copy brings none of its own.
    [attached] = design.methodologies
    assert isinstance(attached, ElephantGoldfish)
    assert (attached.ref, attached.goldfish, attached.model) == (
        method.ref,
        method.goldfish,
        method.model,
    )
    assert (design.name, design.version, design.approval) == ("design", "2", True)
    assert design.gate is design_is_buildable
    [shipped] = design_station(FunctionModel(Designer())).methodologies
    assert isinstance(shipped, ElephantGoldfish) and tuple(shipped.goldfish) == DESIGN_GOLDFISH


def test_no_method_gives_a_plain_designer() -> None:
    designer = Designer()
    plain = design_station(FunctionModel(designer), method=None)

    result = plain.run_sync(MISSION, store=MemoryStore())

    assert plain.methodologies == ()
    assert result.outcome == "waiting" and result.evidence == ()
    assert catalogue(designer.instructions[0]) == ["design-doc", "design-read-code"]
    assert designer.tools[0] == ["ask_user_question", "load_capability"]
    with pytest.raises(FactoryDefinitionError, match="is not a method"):
        design_station(FunctionModel(designer), method="review")  # pyright: ignore[reportArgumentType]


def test_software_factory_takes_a_design_station() -> None:
    model = FunctionModel(Designer())
    design = design_station(model, method=None)

    software = SoftwareFactory(model, design=design, repairs=1)
    ours = Factory("ours", [design_station(model)])

    assert software.stations == (design,)
    assert software.rework == (Rework(design, limit=1, on_limit="escalate"),)
    assert software.methodologies()["design"] == ()
    assert [agent.name for agent in ours.agents()["design"]] == ["designer"]
    run = software.run_sync(MISSION, store=MemoryStore())
    assert run.waiting is not None and run.waiting.kind == "approval"
