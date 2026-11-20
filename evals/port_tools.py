"""The port tools: the stakeholder and delivery ports, served as two tools over the network.

A system that runs as a separate program, such as Claude Code, cannot call the ports
object in the harness's process. It reaches the ports through two tools instead, served
by this process for one run over the Model Context Protocol, at ``/mcp`` on a free port:

* ``ask_stakeholder(questions)`` asks one to four questions and returns one reply for
  each, in order;
* ``deliver(document, status)`` delivers the design.

Each tool calls the same ports object an in-process adapter would, so each logs the same
events. A call that the ports refuse, such as one with five questions or none, is a tool
error the program can read and retry, and logs nothing.

The descriptions say only what each tool does, never when to use it or what to write.
With no output contract, they are the only text a system gets beside the request.

The server runs in a thread of its own, as the second spike did it. A tool call is sent
back to the harness's event loop, so the ports are used from that loop only, as they are
by an in-process adapter. Each run gets a new server on a new port, and the server is
stopped and its socket closed when the run ends.

This module is on the grader side. It imports nothing from the factory.
"""

from __future__ import annotations

import functools
import socket
import threading
from collections.abc import AsyncGenerator, Callable, Coroutine, Sequence
from contextlib import asynccontextmanager
from typing import Any, NamedTuple, Protocol, TypeVar

import anyio
import anyio.from_thread
import anyio.lowlevel
import anyio.to_thread
import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from evals.contract import Ports, Status, check_questions

SERVER_NAME = "ports"
"""The name the tool server goes by. A program sees each tool as ``mcp__ports__<tool>``."""

ASK_TOOL = "ask_stakeholder"
DELIVER_TOOL = "deliver"

ASK_DESCRIPTION = (
    "Ask the stakeholder one to four questions. Returns one reply for each question, "
    "in the same order."
)
"""What ``ask_stakeholder`` says about itself, drafted for the author to confirm."""

DELIVER_DESCRIPTION = (
    "Deliver your design document, in markdown, with a status of delivered or blocked. "
    "Call it once, when you are done."
)
"""What ``deliver`` says about itself, drafted for the author to confirm."""

DELIVERED = "Delivered."
"""What ``deliver`` returns. A later delivery returns the same and changes nothing."""

PATH = "/mcp"
"""Where the server answers, over streamable HTTP."""

LOCAL_HOST = "127.0.0.1"
"""The address the server listens on by default: this machine only."""

START_SECONDS = 10.0
"""How long the server may take to start before the run is given up as broken."""

STOP_SECONDS = 15.0
"""How long the server may take to stop before its thread is left to die with the process."""

T = TypeVar("T")


class PortServerError(RuntimeError):
    """The tool server could not be started."""


class Runner(Protocol):
    """Runs one port call, and returns what it returned."""

    async def __call__(self, work: Callable[[], Coroutine[Any, Any, T]]) -> T: ...


class Address(NamedTuple):
    """Where a tool server listens."""

    host: str
    port: int

    def url(self, host: str | None = None) -> str:
        """The server's address as a URL, reached as ``host``, by default where it listens."""
        return f"http://{host or self.host}:{self.port}{PATH}"


async def _here(work: Callable[[], Coroutine[Any, Any, T]]) -> T:
    return await work()


def port_server(ports: Ports, *, runner: Runner = _here) -> MCPServer:
    """A new tool server named ``ports``, with the two tools over ``ports``.

    ``runner`` runs each port call; by default in the tool's own event loop.
    """
    server = MCPServer(SERVER_NAME, log_level="WARNING")

    async def ask_stakeholder(questions: list[str]) -> list[str]:
        try:
            check_questions(questions)

            async def ask() -> list[str]:
                return [reply.text for reply in await ports.ask_stakeholder(list(questions))]

            return await runner(ask)
        except ValueError as error:
            raise ToolError(str(error)) from error

    async def deliver(document: str, status: Status) -> str:
        async def send() -> None:
            await ports.deliver(document, status)

        try:
            await runner(send)
        except ValueError as error:
            raise ToolError(str(error)) from error
        return DELIVERED

    server.add_tool(ask_stakeholder, name=ASK_TOOL, description=ASK_DESCRIPTION)
    server.add_tool(deliver, name=DELIVER_TOOL, description=DELIVER_DESCRIPTION)
    return server


@asynccontextmanager
async def serve_ports(
    ports: Ports, *, host: str = LOCAL_HOST, also_known_as: Sequence[str] = ()
) -> AsyncGenerator[Address]:
    """Serve the two tools over ``ports`` for one run, and yield where they listen.

    The system picks a free port on ``host``. A request is accepted only when it names
    the server as ``host`` or one of ``also_known_as``, such as the name a container
    reaches this machine by, so a web page cannot reach the tools from elsewhere. When the
    block ends, however it ends, the server is stopped and its socket closed.

    Raises :class:`OSError` when no port can be bound, and :class:`PortServerError` when
    the server does not start.
    """
    runner = back_to(anyio.lowlevel.current_token())
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, 0))
        port = int(sock.getsockname()[1])
        app = port_app(ports, Address(host, port), also_known_as=also_known_as, runner=runner)
        config = uvicorn.Config(app, log_level="warning", log_config=None, ws="none")
        server = uvicorn.Server(config)
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, name="evals-port-tools", daemon=True
        )
        thread.start()
        try:
            await _until_started(server, thread, Address(host, port))
            yield Address(host, port)
        finally:
            # Shielded, so a run stopped by its time limit still stops its server.
            with anyio.CancelScope(shield=True):
                server.should_exit = True
                await anyio.to_thread.run_sync(thread.join, STOP_SECONDS)
    finally:
        sock.close()


def port_app(
    ports: Ports, at: Address, *, also_known_as: Sequence[str] = (), runner: Runner = _here
) -> Starlette:
    """The web app that serves the two tools over ``ports`` at ``/mcp``, for :func:`serve_ports`.

    It accepts a request only when the request names the server as ``at.host`` or one of
    ``also_known_as``, on ``at.port``, and refuses any other with status 421.
    """
    names = [at.host, *also_known_as]
    security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[f"{name}:{at.port}" for name in names],
        allowed_origins=[f"http://{name}:{at.port}" for name in names],
    )
    server = port_server(ports, runner=runner)
    return server.streamable_http_app(
        streamable_http_path=PATH, host=at.host, transport_security=security
    )


def back_to(token: anyio.lowlevel.EventLoopToken) -> Runner:
    """A runner that runs each port call in the event loop ``token`` names."""

    async def run(work: Callable[[], Coroutine[Any, Any, T]]) -> T:
        call = functools.partial(anyio.from_thread.run, work, token=token)
        return await anyio.to_thread.run_sync(call)

    return run


async def _until_started(server: uvicorn.Server, thread: threading.Thread, at: Address) -> None:
    with anyio.move_on_after(START_SECONDS):
        while not server.started:
            if not thread.is_alive():
                raise PortServerError(f"the port tools at {at.url()} stopped as they started")
            await anyio.sleep(0.01)
        stream = await anyio.connect_tcp(at.host, at.port)
        await stream.aclose()
        return
    raise PortServerError(f"the port tools at {at.url()} did not start in {START_SECONDS:g}s")
