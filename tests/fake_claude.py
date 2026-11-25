"""A fake Claude Code for the tests. It plays back recorded tool calls and never calls a model.

The tests start it as ``python tests/fake_claude.py <scenario.json> <claude options...>``.
It reads the request from standard input and the tool-server config named after
``--mcp-config``, calls the port tools over HTTP as the scenario says, and prints JSON lines
shaped like Claude Code's: a start-up event, an assistant and a user event for each tool
call, and a result. What it saw, its options, the request, the names of its ``CLAUDE*`` and
``ANTHROPIC*`` variables and each tool result, is written to the scenario's ``seen`` file.
Each write replaces the file whole, so a test never reads it half written, even when the
adapter kills the fake the moment it reads the start-up event.

A scenario is a JSON object; every key is optional but ``seen``:

* ``seen``: where to write what the fake saw;
* ``status``: the ``ports`` server's status in the start-up event, by default ``connected``;
* ``port_tools``: whether the start-up event lists the two port tools, by default true;
* ``calls``: a list of ``{"tool": name, "arguments": {...}}`` to make, in order;
* ``hang``: seconds to wait before the result, as a program that runs on would;
* ``lines``: raw lines to print in place of the start-up event and the result;
* ``result``: keys laid over the default result line, or ``null`` for no result;
* ``exit``: the exit code, by default 0.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from mcp import Client

PORT_TOOLS = ["mcp__ports__ask_stakeholder", "mcp__ports__deliver"]
DEFAULT_RESULT: dict[str, Any] = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "total_cost_usd": 0.0123,
    "num_turns": 3,
    "usage": {"input_tokens": 1200, "output_tokens": 340},
}


def write_seen(path: Path, seen: dict[str, Any]) -> None:
    """Write ``seen`` to ``path`` through a temporary file, so the file is never half written."""
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(json.dumps(seen), encoding="utf-8")
    os.replace(temporary, path)


def emit(event: dict[str, Any]) -> None:
    print(json.dumps(event), flush=True)


async def call_tools(url: str, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    async with Client(url) as client:
        for call in calls:
            result = await client.call_tool(call["tool"], call["arguments"])
            texts = [getattr(part, "text", "") for part in result.content]
            results.append({"is_error": bool(result.is_error), "texts": texts})
    return results


def main(argv: list[str]) -> int:
    scenario: dict[str, Any] = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    options = argv[2:]
    prompt = sys.stdin.read()
    config_path = options[options.index("--mcp-config") + 1]
    config = json.loads(Path(config_path).read_text(encoding="utf-8"))
    url = config["mcpServers"]["ports"]["url"]
    prefixes = ("CLAUDE", "ANTHROPIC")
    seen: dict[str, Any] = {
        "options": options,
        "prompt": prompt,
        "variables": sorted(name for name in os.environ if name.upper().startswith(prefixes)),
        "cwd": os.getcwd(),
        "url": url,
        "pid": os.getpid(),
        "results": [],
    }
    seen_file = Path(scenario["seen"])
    write_seen(seen_file, seen)

    if "lines" in scenario:
        for line in scenario["lines"]:
            print(line, flush=True)
        time.sleep(float(scenario.get("hang", 0)))
        return int(scenario.get("exit", 0))

    tools = ["Read", "Grep", "Glob"] + (PORT_TOOLS if scenario.get("port_tools", True) else [])
    status = scenario.get("status", "connected")
    emit(
        {
            "type": "system",
            "subtype": "init",
            "cwd": os.getcwd(),
            "tools": tools,
            "mcp_servers": [{"name": "ports", "status": status}],
            "model": options[options.index("--model") + 1],
        }
    )
    calls: list[dict[str, Any]] = scenario.get("calls", [])
    results = asyncio.run(call_tools(url, calls)) if calls else []
    for number, (call, result) in enumerate(zip(calls, results, strict=True), start=1):
        name = f"mcp__ports__{call['tool']}"
        use = {
            "type": "tool_use",
            "id": f"toolu_{number}",
            "name": name,
            "input": call["arguments"],
        }
        emit({"type": "assistant", "message": {"role": "assistant", "content": [use]}})
        back = {
            "type": "tool_result",
            "tool_use_id": f"toolu_{number}",
            "content": [{"type": "text", "text": text} for text in result["texts"]],
            "is_error": result["is_error"],
        }
        emit({"type": "user", "message": {"role": "user", "content": [back]}})
    seen["results"] = results
    write_seen(seen_file, seen)

    time.sleep(float(scenario.get("hang", 0)))
    overlay = scenario.get("result", {})
    if overlay is not None:
        emit({**DEFAULT_RESULT, **overlay})
    return int(scenario.get("exit", 0))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
