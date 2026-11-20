"""The Claude Code adapter: vanilla Claude Code, run as a program through the port tools.

For one run the adapter serves the two port tools from this process with
:func:`~evals.port_tools.serve_ports`, writes a tool-server config that names them
``ports``, and starts Claude Code without a terminal inside a new Docker container. The
container's working folder, ``/workspace``, is the case's snapshot, mounted read-only;
the config is mounted read-only beside it. The request is written to the program's
standard input, unchanged, and is its only prompt.

Claude Code keeps only its read tools, ``Read``, ``Grep`` and ``Glob``, plus the two port
tools, and loads project settings only: never the author's user settings, plugins or
skills. It runs on the pinned model with a spending cap it is never told about beyond
the option itself, and writes its events as JSON lines. The run ends when it stops, which
it does by itself after its last turn.

How the adapter reads the output:

* the program cannot be started, or no result can be read from its output:
  ``infrastructure``;
* the first line, the start-up event, shows the ``ports`` server not connected, or a tool
  list without both port tools: ``infrastructure``. The program is ended at once, because
  Claude Code would otherwise spend a turn and exit 0;
* the result has ``is_error`` and an ``api_error_status``: ``infrastructure``, with the
  status in ``detail``;
* the result's ``subtype`` is ``error_max_budget_usd``: ``budget``. Claude Code checks
  its cap after a turn, so a run can overshoot it by one turn, and exits 1;
* anything else, with or without a delivery: ``finished``.

Cost and tokens come from the result: ``total_cost_usd``, ``usage.input_tokens`` and
``usage.output_tokens``. The harness, not the adapter, stops a run at its time limit; the
adapter then ends the program and removes its container.

Three choices wait on the third spike, which runs Claude Code inside a container. Each is
an option here, set to the smallest choice that could work:

* the address the container reaches this machine by: :data:`CONTAINER_HOST`, with
  ``--add-host`` mapping it to Docker's host gateway;
* the container's network: :data:`CONTAINER_NETWORK`, Docker's default bridge, which
  reaches the port tools and the model provider and does not limit anything else;
* the sign-in token's variable: :data:`TOKEN_VARIABLE`, the only ``CLAUDE*`` or
  ``ANTHROPIC*`` variable the container is given.

With ``container=False`` the program runs directly on this machine, with the same options
and environment. Only the tests use it, to run a fake program offline; the arm always
runs in a container.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from subprocess import PIPE
from typing import Any, Protocol, cast

import anyio
from anyio.abc import ByteReceiveStream, Process
from anyio.streams.buffered import BufferedByteReceiveStream

from evals.contract import Brief, Ports, ReportEnd, RunReport
from evals.port_tools import (
    ASK_TOOL,
    DELIVER_TOOL,
    LOCAL_HOST,
    SERVER_NAME,
    Address,
    PortServerError,
    serve_ports,
)
from evals.sandbox import container_args
from evals.version import CAP_USD, CLAUDE_CODE_MODEL, SANDBOX_IMAGE

CLAUDE_CODE_ARM = "claude-code"

READ_TOOLS = ("Read", "Grep", "Glob")
"""The only Claude Code tools the program keeps: no shell, no writing and no web."""

PORT_TOOLS = tuple(f"mcp__{SERVER_NAME}__{tool}" for tool in (ASK_TOOL, DELIVER_TOOL))
"""The two port tools as Claude Code names them."""

TOKEN_VARIABLE = "CLAUDE_CODE_OAUTH_TOKEN"
"""The variable that carries the subscription seat's long-lived sign-in token.

The third spike confirms how the seat signs in inside a container.
"""

API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
"""The variable passed in place of the token when the arm moves to a metered key."""

SCRUBBED_PREFIXES = ("CLAUDE", "ANTHROPIC")
"""No variable with one of these prefixes reaches the program, but its one sign-in."""

CONTAINER_HOST = "host.docker.internal"
"""The name a container reaches this machine by. The third spike confirms it."""

CONTAINER_NETWORK = "bridge"
"""The container's network: Docker's default, with internet access, so the program can
reach the model provider and the port tools. The third spike settles whether to narrow it.
"""

CONFIG_FOLDER = "/ports"
"""Where the container sees the folder that holds the tool-server config."""

CONFIG_FILE = "mcp.json"

BUDGET_SUBTYPE = "error_max_budget_usd"
"""The result's subtype when Claude Code stopped at its spending cap."""

MAX_LINE_BYTES = 64 * 1024 * 1024
"""The longest output line the adapter reads. A longer one is an infrastructure error."""

STDERR_TAIL = 2000
"""How much of the program's error output an infrastructure error keeps, from the end."""


class Serve(Protocol):
    """Serves the port tools for one run, as :func:`~evals.port_tools.serve_ports` does."""

    def __call__(
        self, ports: Ports, *, host: str = ..., also_known_as: Sequence[str] = ...
    ) -> AbstractAsyncContextManager[Address]: ...


class ProgramFailed(RuntimeError):
    """The program ran, but what it printed shows the run could not take place."""


def claude_options(*, model: str, cap_usd: float, config: str) -> list[str]:
    """Claude Code's options for one run, in order, after the program's name."""
    return [
        "-p",
        "--model",
        model,
        "--output-format",
        "stream-json",
        "--verbose",
        "--max-budget-usd",
        f"{cap_usd:g}",
        "--mcp-config",
        config,
        "--strict-mcp-config",
        "--tools",
        ",".join(READ_TOOLS),
        "--allowedTools",
        ",".join((*READ_TOOLS, *PORT_TOOLS)),
        "--setting-sources",
        "project",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]


def sign_in_variable(*, keep_api_key: bool) -> str:
    """The one ``CLAUDE*`` or ``ANTHROPIC*`` variable the program is given."""
    return API_KEY_VARIABLE if keep_api_key else TOKEN_VARIABLE


def program_environment(environ: Mapping[str, str], *, keep_api_key: bool) -> dict[str, str]:
    """``environ`` without any ``CLAUDE*`` or ``ANTHROPIC*`` variable but the sign-in."""
    keep = sign_in_variable(keep_api_key=keep_api_key)
    return {
        name: value
        for name, value in environ.items()
        if name == keep or not name.upper().startswith(SCRUBBED_PREFIXES)
    }


def docker_command(
    program: Sequence[str],
    *,
    snapshot: Path,
    config_folder: Path,
    name: str,
    keep_api_key: bool,
    docker: Sequence[str] = ("docker",),
    image: str = SANDBOX_IMAGE,
    network: str = CONTAINER_NETWORK,
    container_host: str = CONTAINER_HOST,
) -> list[str]:
    """The ``docker run`` command that starts ``program`` in the run's own container.

    The container has every option :func:`~evals.sandbox.container_args` gives, on
    ``network``, with the config folder mounted read-only at :data:`CONFIG_FOLDER`.
    Standard input stays open for the request. Only the sign-in variable is passed in,
    by name, so its value never appears in the command.
    """
    source = config_folder.resolve()
    return [
        *docker,
        "run",
        "--interactive",
        "--name",
        name,
        *container_args(snapshot, network=network),
        "--mount",
        f"type=bind,source={source},target={CONFIG_FOLDER},readonly",
        "--add-host",
        f"{container_host}:host-gateway",
        "--env",
        sign_in_variable(keep_api_key=keep_api_key),
        image,
        *program,
    ]


class ClaudeCodeAdapter:
    """Vanilla Claude Code, as the arm ``claude-code``, with no added instruction.

    ``command`` starts the program; a test puts a fake program in its place. With
    ``keep_api_key``, the program gets ``ANTHROPIC_API_KEY`` in place of the sign-in token.
    ``cap_usd`` is the spending cap Claude Code is started with. ``container=False`` runs
    the program directly on this machine, for the tests only.
    """

    def __init__(
        self,
        *,
        command: Sequence[str] = ("claude",),
        keep_api_key: bool = False,
        cap_usd: float = CAP_USD,
        model: str = CLAUDE_CODE_MODEL,
        container: bool = True,
        docker: Sequence[str] = ("docker",),
        network: str = CONTAINER_NETWORK,
        server_host: str = LOCAL_HOST,
        container_host: str = CONTAINER_HOST,
        serve: Serve = serve_ports,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._command = tuple(command)
        self._keep_api_key = keep_api_key
        self._cap_usd = cap_usd
        self._model = model
        self._container = container
        self._docker = tuple(docker)
        self._network = network
        self._server_host = server_host
        self._container_host = container_host
        self._serve = serve
        self._environ = environ

    @property
    def name(self) -> str:
        """``claude-code``."""
        return CLAUDE_CODE_ARM

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Serve the port tools, run Claude Code on the request and read what it cost."""
        with tempfile.TemporaryDirectory(prefix="evals-claude-code-") as folder:
            also = (self._container_host,) if self._container else ()
            async with contextlib.AsyncExitStack() as stack:
                try:
                    address = await stack.enter_async_context(
                        self._serve(ports, host=self._server_host, also_known_as=also)
                    )
                except (OSError, PortServerError) as error:
                    return _broken(f"the port tools could not be served: {error}")
                return await self._run_program(brief, Path(folder), address)

    def argv(self, snapshot: Path, config_folder: Path, *, name: str) -> list[str]:
        """The command the adapter runs for one run, given the run's container name."""
        if self._container:
            config = f"{CONFIG_FOLDER}/{CONFIG_FILE}"
        else:
            config = str(config_folder / CONFIG_FILE)
        program = [
            *self._command,
            *claude_options(model=self._model, cap_usd=self._cap_usd, config=config),
        ]
        if not self._container:
            return program
        return docker_command(
            program,
            snapshot=snapshot,
            config_folder=config_folder,
            name=name,
            keep_api_key=self._keep_api_key,
            docker=self._docker,
            network=self._network,
            container_host=self._container_host,
        )

    async def _run_program(self, brief: Brief, config_folder: Path, address: Address) -> RunReport:
        host = self._container_host if self._container else address.host
        config = {"mcpServers": {SERVER_NAME: {"type": "http", "url": address.url(host)}}}
        (config_folder / CONFIG_FILE).write_text(json.dumps(config), encoding="utf-8")
        name = f"evals-claude-code-{uuid.uuid4().hex[:12]}"
        argv = self.argv(brief.snapshot, config_folder, name=name)
        environ = os.environ if self._environ is None else self._environ
        env = program_environment(environ, keep_api_key=self._keep_api_key)
        cwd = None if self._container else brief.snapshot
        try:
            process = await anyio.open_process(
                argv, stdin=PIPE, stdout=PIPE, stderr=PIPE, env=env, cwd=cwd
            )
        except OSError as error:
            return _broken(f"the program could not be started: {type(error).__name__}: {error}")
        errors = bytearray()
        failure = ""
        result: dict[str, Any] | None = None
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(_drain, process.stderr, errors)
                group.start_soon(_send, process, brief.text)
                try:
                    result = await _read_result(process)
                except ProgramFailed as error:
                    failure = str(error)
                    process.kill()
                await process.wait()
                # The error output closes with the program; wait a moment for its last words.
                group.cancel_scope.deadline = anyio.current_time() + 1
        finally:
            with anyio.CancelScope(shield=True):
                await self._stop(process, name)
        if failure:
            return _broken(failure)
        if result is None:
            tail = errors.decode("utf-8", errors="replace")[-STDERR_TAIL:].strip()
            said = f": {tail}" if tail else ""
            return _broken(f"the program exited with code {process.returncode} and no result{said}")
        return report_of(result)

    async def _stop(self, process: Process, name: str) -> None:
        """End the program if it still runs, and remove its container."""
        if process.returncode is None:
            process.kill()
            await process.wait()
        await process.aclose()
        if self._container:
            await anyio.run_process([*self._docker, "rm", "--force", name], check=False)


def report_of(result: Mapping[str, Any]) -> RunReport:
    """The run's report, from Claude Code's result line."""
    usage = _object(result.get("usage"))
    cost = result.get("total_cost_usd")
    turns = result.get("num_turns")
    end: ReportEnd = "finished"
    detail = f"{turns} turns" if isinstance(turns, int) else ""
    if result.get("is_error") and result.get("api_error_status") is not None:
        end = "infrastructure"
        detail = f"the model provider answered {result['api_error_status']}"
    elif result.get("subtype") == BUDGET_SUBTYPE:
        end = "budget"
        detail = "Claude Code stopped at its spending cap"
    return RunReport(
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
        input_tokens=_count(usage.get("input_tokens")),
        output_tokens=_count(usage.get("output_tokens")),
        end=end,
        detail=detail,
    )


def check_start(event: Mapping[str, Any]) -> None:
    """Raise :class:`ProgramFailed` unless the start-up event shows both port tools ready."""
    if event.get("type") != "system" or event.get("subtype") != "init":
        raise ProgramFailed("the program's first line is not its start-up event")
    servers = [_object(server) for server in _list(event.get("mcp_servers"))]
    status = next((s.get("status") for s in servers if s.get("name") == SERVER_NAME), None)
    if status != "connected":
        raise ProgramFailed(f"the port tools are not connected: their status is {status!r}")
    listed = set(_list(event.get("tools")))
    missing = [tool for tool in PORT_TOOLS if tool not in listed]
    if missing:
        raise ProgramFailed(f"the program does not list {', '.join(missing)}")


async def _read_result(process: Process) -> dict[str, Any] | None:
    """Check the first line, then return the last result line, or ``None`` if there is none."""
    if process.stdout is None:
        raise ProgramFailed("the program's output cannot be read")
    lines = BufferedByteReceiveStream(process.stdout)
    result: dict[str, Any] | None = None
    first = True
    ended = False
    while not ended:
        try:
            raw = await lines.receive_until(b"\n", MAX_LINE_BYTES)
        except anyio.IncompleteRead:
            # The output ended; what is left is a last line with no newline, or nothing.
            raw = bytes(lines.buffer)
            ended = True
        except anyio.DelimiterNotFound as error:
            raise ProgramFailed("the program printed a line too long to read") from error
        if not raw.strip():
            continue
        try:
            parsed: object = json.loads(raw)
        except ValueError as error:
            raise ProgramFailed("the program printed a line that is not JSON") from error
        event = _object(parsed)
        if not isinstance(parsed, dict):
            raise ProgramFailed("the program printed a line that is not a JSON object")
        if first:
            check_start(event)
            first = False
        if event.get("type") == "result":
            result = event
    return result


async def _send(process: Process, text: str) -> None:
    """Write the request to the program's standard input, and close it."""
    if process.stdin is None:
        return
    try:
        await process.stdin.send(text.encode("utf-8"))
    except (anyio.BrokenResourceError, anyio.ClosedResourceError, OSError):
        pass
    finally:
        await process.stdin.aclose()


async def _drain(stream: ByteReceiveStream | None, into: bytearray) -> None:
    """Keep what the program writes to its error output, so it never blocks on it."""
    if stream is None:
        return
    try:
        async for chunk in stream:
            into.extend(chunk)
            del into[: max(0, len(into) - 4 * STDERR_TAIL)]
    except (anyio.BrokenResourceError, anyio.ClosedResourceError):
        return


def _object(value: object) -> dict[str, Any]:
    """``value`` when it is a JSON object, and an empty one otherwise."""
    return cast(dict[str, Any], value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    """``value`` when it is a JSON list, and an empty one otherwise."""
    return cast(list[Any], value) if isinstance(value, list) else []


def _count(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _broken(detail: str) -> RunReport:
    return RunReport(
        cost_usd=None, input_tokens=0, output_tokens=0, end="infrastructure", detail=detail
    )
