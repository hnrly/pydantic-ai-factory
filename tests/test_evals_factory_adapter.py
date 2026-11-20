"""The factory adapter: the Design station at full strength, run offline through the ports.

Every model here is scripted. The runs use a local, read-only workspace over the case's
snapshot, so they need no Docker; the tests that start a container are skipped, with the
reason, when Docker is not running or the sandbox image has not been built.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.capabilities import AbstractCapability, LocalWorkspace
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai_harness.filesystem import READ_ONLY_TOOL_NAMES

from evals import experiment, sandbox
from evals.__main__ import main
from evals.adapters import factory as factory_module
from evals.adapters.factory import FactoryAdapter, RecordingModel, factory_arm
from evals.adapters.scripted import designer_replies, function_model, scripted_designer
from evals.cases import CASES, PublicCase, cases
from evals.contract import RunRecord
from evals.facts import FactSheet, load
from evals.grading import grade
from evals.harness import run_case
from evals.simulator import scripted_picker
from evals.version import SANDBOX_IMAGE
from pydantic_ai_factory import MemoryStore

pytestmark = pytest.mark.anyio

ARMS = pytest.mark.parametrize("review", [True, False], ids=["factory", "factory-simple"])

CHECKLIST = [
    {"id": "expiry", "item": "Says what happens to a task when it expires."},
    {"id": "undo", "item": "Lets a person undo a tick."},
    {"id": "order", "item": "Keeps tasks in the order they were added."},
]
RETENTION = {
    "id": "retention",
    "value": "47 days",
    "criticality": "critical",
    "obtainable": True,
    "held_by": "product owner",
    "statement": "Ticked tasks are kept for 47 days.",
    "direct_question": "How long are ticked tasks kept?",
    "keywords": ["kept"],
}
LIMIT = {
    "id": "limit",
    "value": "61 tasks",
    "criticality": "critical",
    "obtainable": True,
    "held_by": "product owner",
    "statement": "A list holds at most 61 tasks.",
    "direct_question": "How many tasks can a list hold?",
    "keywords": ["hold"],
}
UNKNOWN = {
    "id": "legal-hold",
    "value": None,
    "criticality": "critical",
    "obtainable": False,
    "held_by": "legal",
    "statement": "Nobody knows yet whether tasks fall under a legal hold.",
    "direct_question": "Do tasks fall under a legal hold?",
    "keywords": ["legal"],
}
DATABASE = {
    "id": "database",
    "value": "SQLite 3.46",
    "criticality": "critical",
    "obtainable": True,
    "source": "repo",
    "held_by": "engineering",
    "statement": "The app stores its data in SQLite 3.46.",
    "direct_question": "Which database does the app use?",
    "keywords": ["database"],
}
REQUEST = "Build a to-do page."
SKILL_LINE = re.compile(r"^- ([a-z0-9-]+): ", re.MULTILINE)


@asynccontextmanager
async def local(snapshot: Path) -> AsyncGenerator[AbstractCapability[Any]]:
    """The snapshot as a local workspace, for a run that needs no container."""
    yield LocalWorkspace(snapshot)


def sheet_of(challenge_class: str, *facts: dict[str, Any], name: str = "tasks") -> FactSheet:
    return FactSheet.model_validate(
        {
            "case_name": name,
            "challenge_class": challenge_class,
            "canary": "canary-1234abcd5678",
            "fallback_reply": "I don't have anything to add on that.",
            "approval_reply": "I can't sign anything off here.",
            "checklist": CHECKLIST,
            "facts": list(facts),
        }
    )


def write_case(root: Path, sheet: FactSheet, overlay: dict[str, str] | None = None) -> PublicCase:
    """Write a case, its sheet and its overlay into a dev split under ``root``."""
    case = PublicCase(name=sheet.case_name, request=REQUEST, rung="empty")
    split = root / "dev"
    (split / "facts").mkdir(parents=True)
    (split / f"{case.name}.json").write_text(case.model_dump_json(), encoding="utf-8")
    (split / "facts" / f"{case.name}.json").write_text(sheet.model_dump_json(), encoding="utf-8")
    for path, text in (overlay or {}).items():
        file = split / "repo" / case.name / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding="utf-8")
    return case


async def run_arm(
    review: bool,
    case: PublicCase,
    sheet: FactSheet,
    out: Path,
    *,
    model: Any = None,
    cases_root: Path = CASES,
    sandbox: Any = local,
) -> RunRecord:
    adapter = FactoryAdapter(model or scripted_designer(sheet), review=review, sandbox=sandbox)
    return await run_case(
        adapter,
        case,
        sheet,
        arm=adapter.name,
        out=out,
        picker=scripted_picker(sheet),
        cap_usd=0.5,
        time_limit_seconds=120.0,
        reads_files=True,
        cases_root=cases_root,
    )


def kinds(record: RunRecord) -> list[str]:
    return [event.kind for event in record.events]


def is_goldfish(info: AgentInfo) -> bool:
    schema = info.output_tools[0].parameters_json_schema if info.output_tools else {}
    return "findings" in schema.get("properties", {})


class Watched:
    """The scripted designer, with every request it was sent kept for the test to read."""

    def __init__(self, sheet: FactSheet, **settings: Any) -> None:
        self.inner = designer_replies(sheet, **settings)
        self.requests: list[tuple[list[ModelMessage], AgentInfo]] = []

    def __call__(self, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        self.requests.append((list(messages), info))
        return self.inner(messages, info)

    @property
    def model(self) -> Any:
        return function_model(self, name="watched")

    def designer(self) -> list[AgentInfo]:
        return [info for _, info in self.requests if not is_goldfish(info)]

    def tools(self) -> set[str]:
        return {tool.name for _, info in self.requests for tool in info.function_tools}


# The smoke case.


@ARMS
async def test_on_the_smoke_case_each_arm_ends_as_a_verified_design(
    review: bool, tmp_path: Path
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")

    record = await run_arm(review, case, sheet, tmp_path)

    assert record.end == "finished"
    assert grade(record, sheet).outcome == "verified_design"
    assert record.package is not None and record.package.status == "delivered"
    assert "Ticked tasks are kept for 47 days." in record.package.document
    asked = [e.data["question"] for e in record.events if e.kind == "question_asked"]
    assert asked == ["How long are ticked tasks kept?"]
    approvals = [e.data["what"] for e in record.events if e.kind == "adapter_approved"]
    assert approvals == ["design"]
    assert kinds(record) == [
        "brief_sent",
        "question_asked",
        "reply_sent",
        "adapter_approved",
        "delivered",
    ]


@ARMS
async def test_a_round_of_two_questions_reaches_the_stakeholder_port_as_one_call(
    review: bool, tmp_path: Path
) -> None:
    sheet = sheet_of("critical-ambiguity", RETENTION, LIMIT)
    case = write_case(tmp_path / "cases", sheet)

    record = await run_arm(review, case, sheet, tmp_path / "out", cases_root=tmp_path / "cases")

    pairs = [
        (e.kind, e.data["call"])
        for e in record.events
        if e.kind in ("question_asked", "reply_sent")
    ]
    assert pairs == [
        ("question_asked", 1),
        ("reply_sent", 1),
        ("question_asked", 1),
        ("reply_sent", 1),
    ]
    assert grade(record, sheet).outcome == "verified_design"


@ARMS
async def test_the_mission_each_arm_receives_is_the_request_unchanged(
    review: bool, tmp_path: Path
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")
    watched = Watched(sheet)

    await run_arm(review, case, sheet, tmp_path, model=watched.model)

    first, _ = watched.requests[0]
    prompts = [
        part.content
        for message in first
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert prompts == [case.request]


@ARMS
async def test_a_fact_nobody_knows_is_delivered_as_a_correct_block(
    review: bool, tmp_path: Path
) -> None:
    sheet = sheet_of("nobody-knows", UNKNOWN)
    case = write_case(tmp_path / "cases", sheet)

    record = await run_arm(review, case, sheet, tmp_path / "out", cases_root=tmp_path / "cases")

    assert record.package is not None and record.package.status == "blocked"
    assert kinds(record).count("question_asked") == 1
    assert "adapter_approved" not in kinds(record)
    assert grade(record, sheet).outcome == "correct_block"


async def test_an_escalated_finding_is_approved_by_the_adapter_and_then_delivered(
    tmp_path: Path,
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")
    inner = designer_replies(sheet)
    found = "It does not say what happens on blank input."
    critiques: list[int] = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        said = " ".join(str(getattr(p, "content", "")) for m in messages for p in m.parts)
        if is_goldfish(info) and "Stage: critique." in (info.instructions or ""):
            critiques.append(1)
            if len(critiques) == 1:  # the first critique finds a Major; later ones find nothing
                finding = {"severity": "Major", "kind": "gap", "issue": found}
                name = info.output_tools[0].name
                return ModelResponse(parts=[ToolCallPart(name, {"findings": [finding]})])
        if not is_goldfish(info) and "r1-critique-1" in said and "escalated" not in said:
            reason = {"finding_id": "r1-critique-1", "reason": "Only the owner can say."}
            return ModelResponse(parts=[ToolCallPart("escalate", reason, "escalate-1")])
        return inner(messages, info)

    model = function_model(respond, name="escalates")

    record = await run_arm(True, case, sheet, tmp_path, model=model)

    events = [e for e in record.events if e.kind in ("adapter_approved", "delivered")]
    assert [e.kind for e in events] == ["adapter_approved", "delivered"]
    assert events[0].data["what"] == "escalation"
    assert any("r1-critique-1 escalated:" in reason for reason in events[0].data["reasons"])
    assert grade(record, sheet).outcome == "verified_design"


@ARMS
async def test_a_model_that_raises_gives_a_run_that_ends_as_infrastructure(
    review: bool, tmp_path: Path
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")

    def breaks(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        raise RuntimeError("the provider timed out")

    record = await run_arm(
        review, case, sheet, tmp_path, model=function_model(breaks, name="breaks")
    )

    assert record.end == "infrastructure"
    assert record.reruns == 1
    assert record.package is None
    assert grade(record, sheet).outcome == "infrastructure_error"


# What the agents are given.


@ARMS
async def test_no_agent_has_a_tool_that_writes_runs_a_command_or_fetches_a_page(
    review: bool, tmp_path: Path
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")
    watched = Watched(sheet)

    await run_arm(review, case, sheet, tmp_path, model=watched.model)

    allowed = {"ask_person", "contest", "escalate", "load_capability", *READ_ONLY_TOOL_NAMES}
    assert watched.tools()
    assert watched.tools() <= allowed
    assert "read_file" in watched.tools()


async def test_the_simple_designer_is_offered_the_same_skills_and_cannot_contest_or_escalate(
    tmp_path: Path,
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")
    full, simple = Watched(sheet), Watched(sheet)

    await run_arm(True, case, sheet, tmp_path / "full", model=full.model)
    await run_arm(False, case, sheet, tmp_path / "simple", model=simple.model)

    def catalogue(watched: Watched) -> list[str]:
        return sorted(SKILL_LINE.findall(watched.designer()[0].instructions or ""))

    assert catalogue(full) == catalogue(simple)
    assert {"design-doc", "design-read-code"} <= set(catalogue(full))
    assert {"contest", "escalate"} <= {t.name for t in full.designer()[-1].function_tools}
    assert not {"contest", "escalate"} & simple.tools()
    assert not any(is_goldfish(info) for _, info in simple.requests)


@ARMS
async def test_a_repo_fact_is_read_from_the_overlay_and_graded_a_verified_design_with_no_turns(
    review: bool, tmp_path: Path
) -> None:
    sheet = sheet_of("in-the-repo", DATABASE)
    case = write_case(
        tmp_path / "cases", sheet, {"src/db.ts": "export const DB = 'SQLite 3.46';\n"}
    )
    overlay = tmp_path / "cases" / "dev" / "repo" / case.name
    watched = Watched(sheet, overlay=overlay)

    record = await run_arm(
        review, case, sheet, tmp_path / "out", model=watched.model, cases_root=tmp_path / "cases"
    )

    assert "question_asked" not in kinds(record)
    assert record.package is not None
    assert "The app stores its data in SQLite 3.46." in record.package.document
    assert grade(record, sheet).outcome == "verified_design"


# Cost.


async def test_recording_model_keeps_each_response_once_across_a_wait_and_a_resume(
    tmp_path: Path,
) -> None:
    sheet = load("smoke", "todo")
    watched = Watched(sheet)
    recording = RecordingModel(watched.model)
    software = factory_arm(recording, LocalWorkspace(tmp_path), review=False)
    store = MemoryStore()

    run = await software.run(REQUEST, store=store)
    assert run.waiting is not None and run.waiting.kind == "tool-calls"
    requests = run.waiting.requests
    assert requests is not None
    [call] = requests.calls
    run = await software.resume(
        run, requests.build_results(calls={call.tool_call_id: ["Kept for 47 days."]}), store=store
    )
    assert run.waiting is not None and run.waiting.kind == "approval"
    run = await software.resume(run, run.approve(by="test"), store=store)

    assert run.outcome == "passed"
    assert len(recording.responses) == len(watched.requests)
    report = recording.report("finished")
    assert report.cost_usd is None
    assert report.input_tokens > 0 and report.output_tokens > 0


@ARMS
async def test_a_run_on_the_scripted_designer_has_no_cost_and_counts_its_tokens(
    review: bool, tmp_path: Path
) -> None:
    sheet = load("smoke", "todo")
    [case] = cases("smoke")

    record = await run_arm(review, case, sheet, tmp_path)

    assert record.cost_usd is None
    assert record.input_tokens > 0 and record.output_tokens > 0


# The boundary.


def test_the_adapter_builds_both_arms_from_public_names_only() -> None:
    source = Path(factory_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory_packages = ("pydantic_ai_factory", "pydantic_ai_factory_software")

    private = [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.split(".")[0] in factory_packages
        for alias in node.names
        if alias.name.startswith("_") or any(p.startswith("_") for p in node.module.split("."))
    ]
    builds_agent = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr) == "Agent"
    ]

    assert private == []
    assert builds_agent == []


# The command.


def test_offline_the_default_arms_include_the_factory_arms_when_docker_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: True)

    arms, left_out = experiment.default_arms(offline=True)

    assert arms[-2:] == ["factory", "factory-simple"] and left_out == []
    assert experiment.default_arms(offline=False) == (
        ["claude-code", "factory", "factory-simple"],
        [],
    )


def test_offline_without_docker_the_factory_arms_are_left_out_and_a_line_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: False)

    code = main(["--offline", "--out", str(tmp_path)])

    assert code == 0
    assert not (tmp_path / "factory").exists()
    assert (
        "Docker is not running, so the factory, factory-simple arm(s) were left out"
        in (capsys.readouterr().out.splitlines()[-1])
    )


def test_naming_a_factory_arm_without_docker_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: False)

    code = main(["--offline", "--arm", "factory", "--out", str(tmp_path)])

    assert code == 2
    assert "Docker is needed to run the factory arm(s)" in capsys.readouterr().out
    assert not (tmp_path / "factory").exists()


# The sandbox.


async def test_one_container_serves_every_agent_of_a_run_and_has_no_network(
    tmp_path: Path,
) -> None:
    no_context: Any = None

    async with factory_module.docker_sandbox(tmp_path) as workspace:
        assert isinstance(workspace, factory_module.RunSandbox)
        first = workspace.get_workspace(no_context, ref=None)
        again = workspace.get_workspace(no_context, ref=None)

        assert first is not None and first is again
        assert workspace.image == SANDBOX_IMAGE
        assert workspace.allow_internet_access is False
        assert "--network" not in workspace.docker_args
        mount = f"type=bind,source={tmp_path.resolve()},target=/workspace,readonly"
        assert mount in workspace.docker_args
    # No container was started, so there was nothing to remove and no Docker was needed.


# In a container.


def _docker_skip_reason() -> str | None:
    if not sandbox.docker_available():
        return "Docker is not running here, so no container can be started"
    inspect = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, check=False
    )
    if inspect.returncode != 0:
        return f"the image {SANDBOX_IMAGE} is not built; run python -m evals --build-image"
    return None


def needs_docker() -> None:
    reason = _docker_skip_reason()
    if reason is not None:
        pytest.skip(reason)


def test_the_smoke_case_runs_as_both_factory_arms_offline_in_a_container(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    needs_docker()
    before = workspace_containers()

    code = main(
        ["--offline", "--arm", "factory", "--arm", "factory-simple", "--out", str(tmp_path)]
    )

    lines = capsys.readouterr().out.splitlines()
    assert code == 0
    assert [line.split()[0] for line in lines[:2]] == ["factory", "factory-simple"]
    assert all("verified_design" in line for line in lines[:2])
    assert workspace_containers() <= before


def workspace_containers() -> set[str]:
    """The ids of every container Pydantic AI Harness's Docker sandbox made, running or not."""
    listed = subprocess.run(
        ["docker", "ps", "--all", "--quiet", "--filter", "label=ai.pydantic.workspace=true"],
        capture_output=True,
        text=True,
        check=False,
    )
    return set(listed.stdout.split())


@ARMS
async def test_in_a_container_each_arm_reads_the_overlay_and_is_graded_a_verified_design(
    review: bool, tmp_path: Path
) -> None:
    needs_docker()
    sheet = sheet_of("in-the-repo", DATABASE)
    case = write_case(
        tmp_path / "cases", sheet, {"src/db.ts": "export const DB = 'SQLite 3.46';\n"}
    )
    overlay = tmp_path / "cases" / "dev" / "repo" / case.name

    record = await run_arm(
        review,
        case,
        sheet,
        tmp_path / "out",
        model=scripted_designer(sheet, overlay=overlay),
        cases_root=tmp_path / "cases",
        sandbox=factory_module.docker_sandbox,
    )

    assert "question_asked" not in kinds(record)
    assert grade(record, sheet).outcome == "verified_design"
