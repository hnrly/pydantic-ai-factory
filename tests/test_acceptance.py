"""One test for each thing the roadmap says you can do today.

Each test is named after its row in the "Today" table on ``docs/roadmap.md`` and proves
that row end to end through the public API, with scripted models. If a row stops being
true, its test fails. The test for serving a factory as a board runs the example as an
operator would, so it lives in ``examples/software-factory-app/tests/test_acceptance.py``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from pydantic_ai import Agent, Conversation, ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.output import OutputContext
from scripted import (
    DESIGN,
    DESIGN_WITH_BLANK,
    SAMPLE_MODEL,
    SOFTWARE,
    call,
    critique,
    readiness,
    sample_models,
    scripted_model,
    software_designer,
)

from evals import design as evals
from pydantic_ai_factory import (
    Artifact,
    Declined,
    Evidence,
    Factory,
    LocalStore,
    MemoryStore,
    Methodology,
    Mission,
    Rework,
    Signal,
    SQLiteStore,
    StationContext,
    StationInputInvalid,
    Verdict,
    escalate,
    failed,
    gate,
    passed,
    station,
)
from pydantic_ai_factory.methodologies import ElephantGoldfish, GoldfishReport
from pydantic_ai_factory_software import DESIGN_GOLDFISH, Design, SoftwareFactory

MISSION = "A to-do list web page where I can add tasks and tick them off."
BUG = "Ticked tasks come back after a reload"


def agent(name: str, *replies: Any, **options: Any) -> Agent[None, Any]:
    return Agent(scripted_model(replies), name=name, **options)


@gate(version="1")
def mentions_blank_tasks(output: Artifact[str], evidence: Evidence) -> Verdict:
    if "blank" in output.payload.lower():
        return passed("the design says what happens to a blank task")
    return failed("the design never says what happens to a blank task")


def test_you_can_build_a_factory_of_your_own_run_a_mission_through_it_and_approve_the_result() -> (
    None
):
    designer = agent("designer", DESIGN)
    builder = agent("builder", "Built index.html.")
    reviewer = agent("reviewer", "The page matches the design. Nothing is wrong.")

    @station
    async def build(ctx: StationContext, design: str) -> str:
        await ctx.run_agent(builder, design)
        review = await ctx.run_agent(reviewer, design)
        return review.output

    software = Factory("software", [station(designer, approval=True), build])
    store = MemoryStore()

    run = software.run_sync(MISSION, store=store)
    assert run.waiting is not None
    assert (run.waiting.kind, run.waiting.output) == ("approval", DESIGN)

    done = software.resume_sync(run.approve(by="ada"), store=store)
    assert done.outcome == "passed"
    assert done.output == "The page matches the design. Nothing is wrong."
    assert [step.station for step in done.steps] == ["designer", "build"]
    assert done.answers[0].by == "ada"


def test_you_can_run_the_software_factory_on_a_mission_from_python_or_a_terminal(
    tmp_path: Path,
) -> None:
    with sample_models(SOFTWARE):
        software = SoftwareFactory(SAMPLE_MODEL)
    store = MemoryStore()
    run = software.run_sync(MISSION, store=store)
    assert run.waiting is not None and run.waiting.kind == "approval"
    done = software.resume_sync(run.approve(by="ada"), store=store)
    assert done.outcome == "passed" and isinstance(done.output, Design)

    command = shutil.which("factory", path=str(Path(sys.executable).parent))
    assert command is not None, "the factory command is not installed beside this Python"
    finished = subprocess.run(
        [
            command,
            *("run", MISSION, "--offline", "--approve"),
            *("--store", str(tmp_path / "state"), "--out", str(tmp_path / "designs")),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert finished.returncode == 0, finished.stdout + finished.stderr
    [written] = (tmp_path / "designs").iterdir()
    assert written.read_text(encoding="utf-8").startswith("# To-do list")


def test_you_can_declare_typed_stations_that_run_agents_or_none() -> None:
    class Page(BaseModel):
        path: str
        lines: int

    @station
    async def count_lines(ctx: StationContext, html: str) -> Page:
        return Page(path="index.html", lines=len(html.splitlines()))

    designer = agent("designer", DESIGN)

    counted = count_lines.run_sync("<h1>To-do list</h1>\n<ul></ul>", store=MemoryStore())
    drafted = station(designer).run_sync(MISSION, store=MemoryStore())

    assert (counted.outcome, counted.ran, counted.passed) == ("not_judged", True, None)
    assert count_lines.agents == ()
    assert (drafted.outcome, station(designer).agents) == ("not_judged", (designer,))
    with pytest.raises(StationInputInvalid):
        count_lines.run_sync(42, store=MemoryStore())  # pyright: ignore[reportArgumentType]


def test_you_can_judge_their_output_with_plain_code_over_recorded_evidence() -> None:
    class TestRun(BaseModel):
        passed: int
        failed: int

    @gate(version="1", requires=[TestRun])
    def tests_pass(output: Artifact[str], evidence: Evidence) -> Verdict:
        run = evidence.one(TestRun).payload
        return passed("all tests pass") if run.failed == 0 else failed(f"{run.failed} failed")

    def build_with(result: TestRun | None) -> Any:
        @station(name="build", gate=tests_pass)
        async def build(ctx: StationContext, design: str) -> str:
            if result is not None:
                await ctx.record(result)
            return "Built index.html."

        return build.run_sync(DESIGN, store=MemoryStore())

    green = build_with(TestRun(passed=12, failed=0))
    red = build_with(TestRun(passed=11, failed=1))
    silent = build_with(None)

    assert (green.outcome, green.decision.reasons) == ("passed", ("all tests pass",))
    assert (red.outcome, red.decision.reasons) == ("failed", ("1 failed",))
    assert silent.outcome == "did_not_run"
    assert "missing evidence: TestRun" in silent.reason


def test_you_can_join_stations_into_a_factory_with_bounded_rework() -> None:
    fixed = agent("designer", DESIGN, DESIGN_WITH_BLANK)
    design = station(fixed, gate=mentions_blank_tasks)
    builder = agent("builder", "Built index.html.")
    software = Factory("software", [design, station(builder)], rework=[Rework(design, limit=2)])

    run = software.run_sync(MISSION, store=MemoryStore())

    assert run.outcome == "passed"
    assert [(step.station, step.attempt, step.outcome) for step in run.steps] == [
        ("designer", 1, "failed"),
        ("designer", 2, "passed"),
        ("builder", 1, "not_judged"),
    ]

    stubborn = station(agent("designer", DESIGN), gate=mentions_blank_tasks)
    bounded = Factory("software", [stubborn], rework=[Rework(stubborn, limit=2)])
    stopped = bounded.run_sync(MISSION, store=MemoryStore())
    assert (stopped.outcome, stopped.attempts("designer")) == ("failed", 3)


PROCESS = """
import sys

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from pydantic_ai_factory import Factory, LocalStore, station

DESIGN = {design!r}


def reply(messages, info):
    return ModelResponse(parts=[TextPart(DESIGN)])


designer = Agent(FunctionModel(reply), name="designer")
software = Factory("software", [station(designer, approval=True)])
store = LocalStore(sys.argv[1])
if sys.argv[2] == "start":
    print(software.run_sync({mission!r}, store=store).run_id)
else:
    run = store.get_run_sync(sys.argv[2])
    print(software.resume_sync(run.approve(by="ada"), store=store).outcome)
"""


def test_you_can_wait_for_a_person_settle_an_escalation_or_answer_a_tool_call_and_resume_in_another_process(  # noqa: E501
    tmp_path: Path,
) -> None:
    # Approval, answered by a second process that shares only the folder the store keeps.
    script = tmp_path / "todo_factory.py"
    script.write_text(PROCESS.format(design=DESIGN, mission=MISSION), encoding="utf-8")

    def process(*args: str) -> str:
        done = subprocess.run(
            [sys.executable, str(script), str(tmp_path / "store"), *args],
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    run_id = process("start")
    waiting = LocalStore(tmp_path / "store").get_run_sync(run_id)
    assert waiting.waiting is not None and waiting.waiting.kind == "approval"
    assert process(run_id) == "passed"

    # Escalation, settled by the same verbs.
    @gate(version="1")
    def a_person_should_look(output: Artifact[str], evidence: Evidence) -> Verdict:
        return escalate("the design changes what a tick means")

    designer = station(agent("designer", DESIGN), gate=a_person_should_look)
    software = Factory("software", [designer])
    store = MemoryStore()
    run = software.run_sync(MISSION, store=store)
    assert run.waiting is not None and run.waiting.kind == "escalation"
    settled = software.resume_sync(run.approve(by="ada", note="That is fine."), store=store)
    assert settled.outcome == "passed"
    assert settled.answers[-1].model_dump(include={"kind", "overrode"}) == {
        "kind": "approve",
        "overrode": True,
    }

    # A tool call that needs sign-off, answered with Pydantic AI's own types.
    builder = agent(
        "builder",
        call("publish_page", path="index.html"),
        "Published index.html.",
        instructions="Build the page, then publish it.",
    )

    @builder.tool_plain(requires_approval=True)
    def publish_page(path: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"{path} is live."

    publishing = Factory("software", [station(builder)])
    run = publishing.run_sync(MISSION, store=store)
    assert run.waiting is not None and run.waiting.kind == "tool-calls"
    assert str(run.action) == "publish_page(path='index.html')"
    done = publishing.resume_sync(run.allow(by="ada"), store=store)
    assert (done.outcome, done.output) == ("passed", "Published index.html.")


def test_you_can_commit_to_a_mission_or_turn_signals_such_as_a_bug_report_into_one() -> None:
    class BugReport(BaseModel):
        title: str

    def triage(signals: Sequence[Signal[Any]]) -> Mission | Declined:
        bugs = [s.payload for s in signals if isinstance(s.payload, BugReport)]
        if not bugs:
            return Declined("no bug report among the signals")
        return Mission(f"Fix the to-do list page: {bugs[0].title}")

    software = Factory("software", [station(agent("designer", DESIGN))], accept=triage)
    store = MemoryStore()

    committed = Mission(MISSION, success_criteria=("Ticked tasks stay ticked after a reload.",))
    run = software.run_sync(committed, store=store)
    assert run.outcome == "passed" and run.mission_ref is not None
    assert store.get_sync(run.mission_ref, Mission).payload == committed

    mission = software.intake_sync([Signal(BugReport(title=BUG), source="github")], store=store)
    assert isinstance(mission, Mission)
    assert (mission.objective, len(mission.signals)) == (f"Fix the to-do list page: {BUG}", 1)
    fixed = software.run_sync(mission, store=store)
    assert fixed.outcome == "passed" and fixed.outcome_signal is not None

    declined = software.intake_sync([Signal("a question about pricing")], store=store)
    assert isinstance(declined, Declined)


class ScenarioCheck(BaseModel):
    not_scenarios: list[str]


class Scenarios(BaseModel):
    summary: str
    acceptance_criteria: list[str]


def is_scenario(criterion: str) -> bool:
    return re.match(r"(?is)given .+ when .+ then .+", criterion) is not None


class BehaviourDriven(Methodology):
    name = "behaviour-driven-development"
    version = "1"
    evidence = (ScenarioCheck,)

    def get_instructions(self) -> str:
        return "Write every acceptance criterion as: Given ..., when ..., then ...."

    async def after_output_process(
        self, ctx: RunContext[Any], *, output_context: OutputContext, output: Any
    ) -> Any:
        not_scenarios = [c for c in output.acceptance_criteria if not is_scenario(c)]
        await self.record(ctx, ScenarioCheck(not_scenarios=not_scenarios))
        if not_scenarios:
            raise ModelRetry("Rewrite these as Given/When/Then: " + "; ".join(not_scenarios))
        return output

    def judge(self, output: Artifact[Any], evidence: Evidence) -> Verdict:
        if evidence.of(ScenarioCheck)[-1].payload.not_scenarios:
            return failed("some acceptance criteria are not scenarios")
        return passed("every acceptance criterion is a scenario")


def test_you_can_write_a_way_of_working_as_a_capability_that_a_station_enforces() -> None:
    scenario = "Given an empty list, when I press Enter on a blank task, then nothing is added."
    method = BehaviourDriven()
    designer = agent(
        "designer",
        {"summary": DESIGN, "acceptance_criteria": ["Blank tasks are not added."]},
        {"summary": DESIGN, "acceptance_criteria": [scenario]},
        output_type=Scenarios,
        capabilities=[method],
    )
    design = station(designer)
    software = Factory("software", [design])

    result = design.run_sync(MISSION, store=MemoryStore())

    assert software.methodologies() == {"designer": (method,)}
    assert result.outcome == "passed"
    assert result.decision is not None
    assert result.decision.reasons == ("every acceptance criterion is a scenario",)
    assert [str(m) for m in result.decision.methodologies] == ["behaviour-driven-development@1"]
    assert len(result.evidence) == 2


def test_you_can_have_work_reviewed_by_fresh_goldfish_revised_in_the_authors_own_conversation() -> (
    None
):
    def routed(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        schema = info.output_tools[0].parameters_json_schema if info.output_tools else {}
        if "findings" not in schema.get("properties", {}):
            return software_designer(messages, info)
        stage = info.instructions or ""
        if "Stage: critique" in stage:
            return critique(messages, info)
        if "Stage: readiness" in stage:
            return readiness(messages, info)
        explained: dict[str, Any] = {"paraphrase": "A to-do list page.", "findings": []}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, explained)])

    designer = Agent(
        FunctionModel(routed),
        name="designer",
        output_type=Design,
        capabilities=[ElephantGoldfish(goldfish=DESIGN_GOLDFISH)],
    )
    store = MemoryStore()

    result = station(designer).run_sync(MISSION, store=store)

    assert result.outcome == "passed"
    reports = [store.get_sync(ref, GoldfishReport).payload for ref in result.evidence]
    assert [(r.round, [f.severity for f in r.findings]) for r in reports] == [
        (1, ["Major"]),
        (2, []),
    ]
    conversation = store.get_sync(result.conversations["designer"], Conversation).payload
    said = json.dumps([m.parts for m in conversation.messages], default=str)
    assert "Goldfish review, round 1 of 2" in said
    assert len({ref.digest for ref in result.conversations.values()}) == 1


def test_you_can_see_a_run_as_a_trace() -> None:
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        provider = TracerProvider()
        trace.set_tracer_provider(provider)
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    software = Factory("software", [station(agent("designer", DESIGN), approval=True)])
    store = MemoryStore()
    run = software.run_sync(MISSION, store=store)
    software.resume_sync(run.approve(by="ada"), store=store)

    names = [span.name for span in exporter.get_finished_spans()]
    assert "factory software" in names
    assert "station designer" in names
    assert "agent designer" in names
    assert "answer approval approved" in names
    exporter.shutdown()


def test_you_can_score_a_methodology_against_doing_without_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "results.json"

    code = evals.main(["--offline", "--out", str(out)])

    results = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and results["failures"] == 0
    assert results["with_review"]["rounds"] > results["without_review"]["rounds"] == 0
    assert (
        results["with_review"]["CriteriaTiedToTheMission"]
        > results["without_review"]["CriteriaTiedToTheMission"]
    )
    assert "with review" in capsys.readouterr().out


INBOX_PROCESS = """
import sys

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from pydantic_ai_factory import Factory, SQLiteStore, station


def reply(messages, info):
    return ModelResponse(parts=[TextPart({design!r})])


designer = Agent(FunctionModel(reply), name="designer")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore(sys.argv[1])
for run in store.list_runs_sync(outcome="waiting", order="oldest"):
    print(run.run_id, software.resume_sync(run.approve(by="ada"), store=store).outcome)
"""


def test_you_can_find_the_runs_waiting_for_a_person_from_any_process_that_shares_the_store(
    tmp_path: Path,
) -> None:
    software = Factory("software", [station(agent("designer", DESIGN), approval=True)])
    store = SQLiteStore(tmp_path / "factory.db")
    started = software.run_sync(MISSION, store=store)
    assert started.outcome == "waiting"

    # A second process opens its own store on the same file, finds the run and approves it.
    script = tmp_path / "inbox.py"
    script.write_text(INBOX_PROCESS.format(design=DESIGN), encoding="utf-8")
    answered = subprocess.run(
        [sys.executable, str(script), str(tmp_path / "factory.db")],
        capture_output=True,
        text=True,
        check=True,
    )

    assert answered.stdout.split() == [started.run_id, "passed"]
    assert store.get_run_sync(started.run_id).outcome == "passed"
    assert store.list_runs_sync(outcome="waiting") == []
