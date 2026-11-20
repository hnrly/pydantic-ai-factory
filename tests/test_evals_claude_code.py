"""The port tools and the Claude Code adapter, tested offline against a fake program.

``tests/fake_claude.py`` stands in for Claude Code: it plays back recorded tool calls and
prints lines shaped like Claude Code's. It never calls a model, and the real ``claude``
program is never started here.

Three kinds of test:

* the port tools, called in-process through the tool protocol's in-memory client, so they
  need no network;
* the adapter with the fake program, run directly on this machine, for everything that
  does not reach the port tools over HTTP. These replace the tool server with one that
  listens nowhere;
* the adapter with the fake program over HTTP, which needs a local port to listen on.
  They are skipped, with the reason, where listening is refused.

The fake program runs directly on this machine, not in the container the real one runs
in: the sandbox image holds no Python, and Docker may not be running. The container's
command is checked as data instead. The ``live`` test runs the smoke case through the
real program in its container, and is skipped unless asked for.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anyio
import anyio.lowlevel
import anyio.to_thread
import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from pydantic_ai import models

from evals import experiment
from evals.__main__ import main
from evals.adapters.claude_code import (
    CLAUDE_CODE_ARM,
    CONFIG_FOLDER,
    PORT_TOOLS,
    ClaudeCodeAdapter,
    Serve,
    claude_options,
    program_environment,
)
from evals.cases import CASES, PublicCase, cases
from evals.contract import Brief, Ports, RunRecord, RunReport, brief_for
from evals.events import EventLog
from evals.facts import FactSheet, load
from evals.grading import grade
from evals.harness import RunPorts, run_case
from evals.port_tools import (
    ASK_DESCRIPTION,
    DELIVER_DESCRIPTION,
    DELIVERED,
    Address,
    back_to,
    port_app,
    port_server,
    serve_ports,
)
from evals.sandbox import WORKSPACE, docker_available
from evals.simulator import Stakeholder, haiku_picker, scripted_picker
from evals.version import CAP_USD, CLAUDE_CODE_MODEL, SANDBOX_IMAGE, TIME_LIMIT_SECONDS

pytestmark = pytest.mark.anyio

FAKE = Path(__file__).parent / "fake_claude.py"
QUESTION = "How long are ticked tasks kept?"
ANSWER = "Ticked tasks are kept for 47 days."
DESIGN = f"# To-do page\n\nA page of tasks.\n\n{ANSWER}\n"


def can_listen() -> bool:
    """Whether this process may listen on a local port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
    except OSError:
        return False
    return True


needs_listening = pytest.mark.skipif(
    not can_listen(),
    reason="listening on a local port is refused here, so the port tools cannot be served",
)


@asynccontextmanager
async def nowhere(
    ports: Ports, *, host: str = "127.0.0.1", also_known_as: Sequence[str] = ()
) -> AsyncGenerator[Address]:
    """A tool server that listens nowhere, for a fake program that calls no tool."""
    yield Address(host, 9)


def fake(tmp_path: Path, **scenario: Any) -> tuple[list[str], Path]:
    """The command for the fake program on ``scenario``, and where it writes what it saw."""
    seen = tmp_path / "seen.json"
    file = tmp_path / "scenario.json"
    file.write_text(json.dumps({"seen": str(seen), **scenario}), encoding="utf-8")
    return [sys.executable, str(FAKE), str(file)], seen


def adapter(command: Sequence[str], *, serve: Serve = nowhere, **options: Any) -> ClaudeCodeAdapter:
    return ClaudeCodeAdapter(command=command, container=False, serve=serve, **options)


async def run_fake(
    tmp_path: Path,
    sheet: FactSheet,
    case: PublicCase,
    *,
    serve: Serve = nowhere,
    time_limit_seconds: float = 60.0,
    **scenario: Any,
) -> tuple[RunRecord, Path]:
    command, seen = fake(tmp_path, **scenario)
    record = await run_case(
        adapter(command, serve=serve),
        case,
        sheet,
        arm=CLAUDE_CODE_ARM,
        out=tmp_path / "out",
        picker=scripted_picker(sheet),
        cap_usd=CAP_USD,
        time_limit_seconds=time_limit_seconds,
        reads_files=True,
    )
    return record, seen


def smoke() -> tuple[FactSheet, PublicCase]:
    [case] = cases("smoke")
    return load("smoke", "todo"), case


def ports_for(sheet: FactSheet, folder: Path) -> tuple[RunPorts, EventLog]:
    log = EventLog(folder / "events.jsonl", run="claude-code/todo/1")
    return RunPorts(Stakeholder(sheet, scripted_picker(sheet), log), log), log


def brief(folder: Path) -> Brief:
    _, case = smoke()
    folder.mkdir(parents=True, exist_ok=True)
    return brief_for(case.name, case.request, folder)


def no_ports(tmp_path: Path) -> Ports:
    """Ports for a run whose fake program calls no tool."""
    sheet, _ = smoke()
    return ports_for(sheet, tmp_path / "ports")[0]


def tool_texts(result: Any) -> list[str]:
    return [getattr(part, "text", "") for part in result.content]


# The port tools, in-process.


async def test_the_two_tools_describe_themselves_in_neutral_words_and_nothing_more(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, _ = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        listed = await client.list_tools()

    described = {tool.name: tool.description for tool in listed.tools}
    assert described == {"ask_stakeholder": ASK_DESCRIPTION, "deliver": DELIVER_DESCRIPTION}
    assert ASK_DESCRIPTION == (
        "Ask the stakeholder one to four questions. Returns one reply for each question, "
        "in the same order."
    )
    assert DELIVER_DESCRIPTION == (
        "Deliver your design document, in markdown, with a status of delivered or blocked. "
        "Call it once, when you are done."
    )


async def test_three_questions_in_one_tool_call_get_three_replies_in_order_under_one_call_number(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)
    questions = [QUESTION, "How long can a task title be?", "What colour is the page?"]

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("ask_stakeholder", {"questions": questions})

    assert not result.is_error
    assert result.structured_content == {
        "result": [ANSWER, "A task title can be up to 83 characters.", sheet.fallback_reply]
    }
    pairs = [(e.kind, e.data["call"], e.data["question"]) for e in log.events]
    assert pairs == [
        ("question_asked", 1, questions[0]),
        ("reply_sent", 1, questions[0]),
        ("question_asked", 1, questions[1]),
        ("reply_sent", 1, questions[1]),
        ("question_asked", 1, questions[2]),
        ("reply_sent", 1, questions[2]),
    ]


@pytest.mark.parametrize("count", [0, 5], ids=["none", "five"])
async def test_a_call_of_five_questions_or_of_none_gets_a_tool_error_and_logs_nothing(
    count: int, tmp_path: Path
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("ask_stakeholder", {"questions": [QUESTION] * count})

    assert result.is_error
    [text] = tool_texts(result)
    assert text.endswith(f"a stakeholder call carries one to 4 questions, not {count}")
    assert log.events == ()


async def test_each_tool_logs_the_same_events_as_the_in_process_port_call(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    by_tool, tool_log = ports_for(sheet, tmp_path / "tool")
    in_process, port_log = ports_for(sheet, tmp_path / "port")

    async with Client(port_server(by_tool)) as client:
        asked = await client.call_tool("ask_stakeholder", {"questions": [QUESTION]})
        first = await client.call_tool("deliver", {"document": DESIGN, "status": "delivered"})
        again = await client.call_tool("deliver", {"document": "Later.", "status": "blocked"})
    await in_process.ask_stakeholder([QUESTION])
    await in_process.deliver(DESIGN, "delivered")
    await in_process.deliver("Later.", "blocked")

    def logged(log: EventLog) -> list[tuple[str, dict[str, Any]]]:
        return [(event.kind, event.data) for event in log.events]

    assert logged(tool_log) == logged(port_log)
    assert asked.structured_content == {"result": [ANSWER]}
    assert tool_texts(first) == [DELIVERED] and tool_texts(again) == [DELIVERED]
    assert by_tool.package == in_process.package


async def test_a_status_that_is_neither_delivered_nor_blocked_gets_a_tool_error(
    tmp_path: Path,
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)

    async with Client(port_server(ports)) as client:
        result = await client.call_tool("deliver", {"document": DESIGN, "status": "approved"})

    assert result.is_error
    assert log.events == () and ports.package is None


@pytest.mark.parametrize(
    ("host", "accepted"),
    [("127.0.0.1", True), ("host.docker.internal", True), ("elsewhere.example", False)],
)
async def test_the_web_app_answers_only_requests_that_name_the_server_as_it_is_known(
    host: str, accepted: bool, tmp_path: Path
) -> None:
    sheet, _ = smoke()
    ports, log = ports_for(sheet, tmp_path)
    app = port_app(ports, Address("127.0.0.1", 5555), also_known_as=["host.docker.internal"])
    asked: list[str] = []

    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)) as http,
    ):
        transport = streamable_http_client(f"http://{host}:5555/mcp", http_client=http)
        try:
            async with Client(transport) as client:
                result = await client.call_tool("ask_stakeholder", {"questions": [QUESTION]})
                asked = tool_texts(result)
        except Exception:
            asked = []

    assert bool(asked) == accepted
    assert len(log.events) == (2 if accepted else 0)


async def test_a_port_call_from_the_servers_thread_runs_in_the_harness_event_loop() -> None:
    runner = back_to(anyio.lowlevel.current_token())
    harness = threading.get_ident()

    async def where() -> int:
        return threading.get_ident()

    def in_the_servers_thread() -> int:
        return asyncio.run(runner(where))

    ran_in = await anyio.to_thread.run_sync(in_the_servers_thread)

    assert ran_in == harness


class InProcessProgram:
    """A system that plays back tool calls through the port tools, in this process.

    It stands in for the fake program where no port can be listened on: the same tools,
    reached through the in-memory client instead of HTTP.
    """

    name = CLAUDE_CODE_ARM

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        self.calls = calls

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        async with Client(port_server(ports)) as client:
            for tool, arguments in self.calls:
                await client.call_tool(tool, arguments)
        return RunReport(cost_usd=0.01, input_tokens=1, output_tokens=1, end="finished")


@pytest.mark.parametrize(
    "document",
    [DESIGN, f"{ANSWER} That is all."],
    ids=["with-headings", "no-headings"],
)
async def test_asking_and_delivering_through_the_tools_is_graded_a_verified_design(
    document: str, tmp_path: Path
) -> None:
    sheet, case = smoke()
    program = InProcessProgram(
        [
            ("ask_stakeholder", {"questions": [QUESTION]}),
            ("deliver", {"document": document, "status": "delivered"}),
        ]
    )

    record = await run_case(
        program,
        case,
        sheet,
        arm=CLAUDE_CODE_ARM,
        out=tmp_path,
        picker=scripted_picker(sheet),
        cap_usd=CAP_USD,
        time_limit_seconds=60.0,
        reads_files=True,
    )

    assert grade(record, sheet).outcome == "verified_design"
    assert [e.kind for e in record.events] == [
        "brief_sent",
        "question_asked",
        "reply_sent",
        "delivered",
    ]
    assert not any(e.kind == "adapter_approved" for e in record.events)


# The adapter, with the fake program and no tool server.


def test_the_program_sees_the_port_tools_under_the_server_name_ports() -> None:
    assert PORT_TOOLS == ("mcp__ports__ask_stakeholder", "mcp__ports__deliver")


async def test_the_adapter_starts_claude_code_with_the_options_in_the_designs_order(
    tmp_path: Path,
) -> None:
    command, seen = fake(tmp_path)

    await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    options = json.loads(seen.read_text(encoding="utf-8"))["options"]
    config = options[options.index("--mcp-config") + 1]
    assert options == [
        "-p",
        "--model",
        CLAUDE_CODE_MODEL,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        "0.5",
        "--mcp-config",
        config,
        "--strict-mcp-config",
        "--tools",
        "Read,Grep,Glob",
        "--allowedTools",
        "Read,Grep,Glob,mcp__ports__ask_stakeholder,mcp__ports__deliver",
        "--setting-sources",
        "project",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    assert CLAUDE_CODE_MODEL == "claude-opus-5-5"
    assert options == claude_options(model=CLAUDE_CODE_MODEL, cap_usd=CAP_USD, config=config)


async def test_the_config_names_the_port_tools_as_the_only_server(tmp_path: Path) -> None:
    command, seen = fake(tmp_path)

    await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    assert json.loads(seen.read_text(encoding="utf-8"))["url"] == "http://127.0.0.1:9/mcp"


async def test_the_request_reaches_the_program_on_standard_input_unchanged(
    tmp_path: Path,
) -> None:
    command, seen = fake(tmp_path)
    given = brief(tmp_path / "snapshot")

    await adapter(command).run(given, no_ports(tmp_path))

    saw = json.loads(seen.read_text(encoding="utf-8"))
    assert saw["prompt"] == given.text == cases("smoke")[0].request
    assert Path(saw["cwd"]).resolve() == given.snapshot.resolve()


@pytest.mark.parametrize(
    ("keep_api_key", "passed"),
    [(False, "CLAUDE_CODE_OAUTH_TOKEN"), (True, "ANTHROPIC_API_KEY")],
    ids=["token", "api-key"],
)
async def test_the_program_gets_no_claude_or_anthropic_variable_but_its_sign_in(
    keep_api_key: bool, passed: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in (
        "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CONFIG_DIR",
        "CLAUDECODE",
    ):
        monkeypatch.setenv(name, "set-by-the-test")
    command, seen = fake(tmp_path)

    await adapter(command, keep_api_key=keep_api_key).run(
        brief(tmp_path / "snapshot"), no_ports(tmp_path)
    )

    assert json.loads(seen.read_text(encoding="utf-8"))["variables"] == [passed]


def test_the_environment_keeps_everything_else() -> None:
    environ = {
        "PATH": "/bin",
        "HOME": "/var/empty",
        "CLAUDE_CODE_OAUTH_TOKEN": "t",
        "ANTHROPIC_X": "y",
    }

    assert program_environment(environ, keep_api_key=False) == {
        "PATH": "/bin",
        "HOME": "/var/empty",
        "CLAUDE_CODE_OAUTH_TOKEN": "t",
    }


def test_in_its_container_the_program_sees_the_snapshot_read_only_and_the_config_beside_it(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    config_folder = tmp_path / "config"
    config_folder.mkdir()

    argv = ClaudeCodeAdapter().argv(snapshot, config_folder, name="evals-claude-code-1")

    image = argv.index(SANDBOX_IMAGE)
    docker, program = argv[:image], argv[image + 1 :]
    assert docker == [
        "docker",
        "run",
        "--interactive",
        "--name",
        "evals-claude-code-1",
        "--rm",
        "--mount",
        f"type=bind,source={snapshot.resolve()},target={WORKSPACE},readonly",
        "--workdir",
        WORKSPACE,
        "--network",
        "bridge",
        "--mount",
        f"type=bind,source={config_folder.resolve()},target={CONFIG_FOLDER},readonly",
        "--add-host",
        "host.docker.internal:host-gateway",
        "--env",
        "CLAUDE_CODE_OAUTH_TOKEN",
    ]
    assert program == [
        "claude",
        *claude_options(model=CLAUDE_CODE_MODEL, cap_usd=CAP_USD, config="/ports/mcp.json"),
    ]


async def test_cost_tokens_and_turns_come_from_the_result_line(tmp_path: Path) -> None:
    command, _ = fake(tmp_path)

    report = await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    assert report == RunReport(
        cost_usd=0.0123, input_tokens=1200, output_tokens=340, end="finished", detail="3 turns"
    )


async def test_a_run_that_stops_without_delivering_is_graded_a_design_failure(
    tmp_path: Path,
) -> None:
    sheet, case = smoke()

    record, _ = await run_fake(tmp_path, sheet, case)

    assert record.end == "finished" and record.package is None
    assert record.cost_usd == 0.0123
    assert grade(record, sheet).outcome == "design_failure"


async def test_a_result_at_the_spending_cap_is_graded_budget_exhausted(tmp_path: Path) -> None:
    sheet, case = smoke()

    record, _ = await run_fake(
        tmp_path,
        sheet,
        case,
        result={"subtype": "error_max_budget_usd", "is_error": True, "total_cost_usd": 0.53},
        exit=1,
    )

    assert record.end == "budget" and record.reruns == 0
    assert record.cost_usd == 0.53
    assert grade(record, sheet).outcome == "budget_exhausted"


async def test_a_model_provider_error_in_the_result_is_an_infrastructure_error_with_its_status(
    tmp_path: Path,
) -> None:
    command, _ = fake(
        tmp_path,
        result={"subtype": "success", "is_error": True, "api_error_status": 529},
        exit=1,
    )

    report = await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    assert report.end == "infrastructure"
    assert report.detail == "the model provider answered 529"


async def test_a_command_that_does_not_exist_gives_an_infrastructure_error(
    tmp_path: Path,
) -> None:
    sheet, case = smoke()
    missing = ClaudeCodeAdapter(
        command=[str(tmp_path / "no-such-claude")], container=False, serve=nowhere
    )

    record = await run_case(
        missing,
        case,
        sheet,
        arm=CLAUDE_CODE_ARM,
        out=tmp_path,
        picker=scripted_picker(sheet),
        cap_usd=CAP_USD,
        time_limit_seconds=60.0,
        reads_files=True,
    )

    assert record.end == "infrastructure" and record.reruns == 1
    assert record.detail.startswith("the program could not be started: FileNotFoundError")
    assert grade(record, sheet).outcome == "infrastructure_error"


@pytest.mark.parametrize(
    "scenario",
    [{"status": "failed", "port_tools": False}, {"status": "connected", "port_tools": False}],
    ids=["server-failed", "tools-missing"],
)
async def test_a_first_line_that_shows_the_tool_server_missing_ends_the_program_at_once(
    scenario: dict[str, Any], tmp_path: Path
) -> None:
    command, seen = fake(tmp_path, hang=30, **scenario)
    started = time.monotonic()

    report = await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    assert time.monotonic() - started < 15
    assert report.end == "infrastructure"
    assert "port tools are not connected" in report.detail or "does not list" in report.detail
    assert not _alive(json.loads(seen.read_text(encoding="utf-8"))["pid"])


@pytest.mark.parametrize(
    ("lines", "says"),
    [
        (["not json"], "not JSON"),
        (['{"type": "result"}'], "not its start-up event"),
        ([], "no result"),
    ],
    ids=["not-json", "no-start-up", "nothing"],
)
async def test_output_that_cannot_be_read_is_an_infrastructure_error(
    lines: list[str], says: str, tmp_path: Path
) -> None:
    command, _ = fake(tmp_path, lines=lines, exit=2)

    report = await adapter(command).run(brief(tmp_path / "snapshot"), no_ports(tmp_path))

    assert report.end == "infrastructure" and says in report.detail


async def test_a_tool_server_that_cannot_be_served_is_an_infrastructure_error(
    tmp_path: Path,
) -> None:
    @asynccontextmanager
    async def refused(
        ports: Ports, *, host: str = "127.0.0.1", also_known_as: Sequence[str] = ()
    ) -> AsyncGenerator[Address]:
        raise PermissionError(1, "Operation not permitted")
        yield Address(host, 0)  # pragma: no cover

    command, seen = fake(tmp_path)

    report = await adapter(command, serve=refused).run(
        brief(tmp_path / "snapshot"), no_ports(tmp_path)
    )

    assert report.end == "infrastructure"
    assert report.detail.startswith("the port tools could not be served")
    assert not seen.exists()


async def test_the_time_limit_ends_the_program(tmp_path: Path) -> None:
    sheet, case = smoke()

    record, seen = await run_fake(tmp_path, sheet, case, hang=30, time_limit_seconds=3.0)

    assert record.end == "timeout"
    assert grade(record, sheet).outcome == "budget_exhausted"
    assert not _alive(json.loads(seen.read_text(encoding="utf-8"))["pid"])


# The adapter, with the fake program over HTTP.


@needs_listening
async def test_a_fake_program_that_asks_and_delivers_is_graded_a_verified_design(
    tmp_path: Path,
) -> None:
    sheet, case = smoke()

    record, seen = await run_fake(
        tmp_path,
        sheet,
        case,
        serve=serve_ports,
        calls=[
            {"tool": "ask_stakeholder", "arguments": {"questions": [QUESTION]}},
            {"tool": "deliver", "arguments": {"document": DESIGN, "status": "delivered"}},
        ],
    )

    assert record.end == "finished"
    assert grade(record, sheet).outcome == "verified_design"
    assert [e.kind for e in record.events] == [
        "brief_sent",
        "question_asked",
        "reply_sent",
        "delivered",
    ]
    results = json.loads(seen.read_text(encoding="utf-8"))["results"]
    assert results[1] == {"is_error": False, "texts": [DELIVERED]}


@needs_listening
async def test_over_http_three_questions_get_three_replies_and_five_get_a_tool_error(
    tmp_path: Path,
) -> None:
    sheet, case = smoke()
    three = [QUESTION, "How long can a task title be?", "What colour is the page?"]

    record, seen = await run_fake(
        tmp_path,
        sheet,
        case,
        serve=serve_ports,
        calls=[
            {"tool": "ask_stakeholder", "arguments": {"questions": three}},
            {"tool": "ask_stakeholder", "arguments": {"questions": [QUESTION] * 5}},
        ],
    )

    results = json.loads(seen.read_text(encoding="utf-8"))["results"]
    assert not results[0]["is_error"] and results[1]["is_error"]
    calls = {e.data["call"] for e in record.events if e.kind == "question_asked"}
    assert calls == {1}
    assert [e.data["question"] for e in record.events if e.kind == "question_asked"] == three


@needs_listening
async def test_after_a_run_the_port_is_closed_and_two_runs_get_two_ports(tmp_path: Path) -> None:
    sheet, _ = smoke()
    ports, _ = ports_for(sheet, tmp_path)
    used: list[Address] = []

    for _ in range(2):
        async with serve_ports(ports) as address:
            async with Client(address.url()) as client:
                listed = await client.list_tools()
            assert {tool.name for tool in listed.tools} == {"ask_stakeholder", "deliver"}
            used.append(address)

    assert used[0].port != used[1].port
    for address in used:
        with pytest.raises(OSError):
            await anyio.connect_tcp(address.host, address.port)


# The command.


def test_the_claude_code_arm_reads_files_and_is_refused_offline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert CLAUDE_CODE_ARM in experiment.SANDBOX_ARMS

    code = main(["--offline", "--arm", "claude-code", "--out", str(tmp_path)])

    assert code == 2
    assert "not with --offline" in capsys.readouterr().out
    assert not (tmp_path / "claude-code").exists()


def test_naming_the_claude_code_arm_without_docker_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(experiment, "docker_available", lambda: False)

    code = main(["--arm", "claude-code", "--out", str(tmp_path)])

    assert code == 2
    assert "Docker is needed to run the claude-code arm(s)" in capsys.readouterr().out


# The real program, by hand.


def _live_skip_reason() -> str | None:
    if shutil.which("claude") is None:
        return "Claude Code is not on the path"
    status = subprocess.run(
        ["claude", "auth", "status"], capture_output=True, text=True, timeout=60, check=False
    )
    if status.returncode != 0:
        return "`claude auth status` does not report a signed-in account"
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return "CLAUDE_CODE_OAUTH_TOKEN is not set, so the container cannot sign in"
    if not docker_available():
        return "Docker is not running"
    image = subprocess.run(
        ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, check=False
    )
    if image.returncode != 0:
        return f"the sandbox image {SANDBOX_IMAGE} is not built; run python -m evals --build-image"
    return None


@pytest.mark.live
async def test_the_smoke_case_runs_through_the_real_claude_code_in_its_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reason = _live_skip_reason()
    if reason is not None:
        pytest.skip(reason)
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    sheet, case = smoke()

    record = await run_case(
        ClaudeCodeAdapter(),
        case,
        sheet,
        arm=CLAUDE_CODE_ARM,
        out=tmp_path,
        picker=haiku_picker(),
        cap_usd=CAP_USD,
        time_limit_seconds=TIME_LIMIT_SECONDS,
        reads_files=True,
        cases_root=CASES,
    )

    assert record.end in ("finished", "budget"), record.detail
    assert record.cost_usd is not None
    print(f"claude-code on the smoke case: {grade(record, sheet).outcome}, ${record.cost_usd}")


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
