"""A factory of your own, swapped in for ``app/factory.py``, runs through the same API.

Its one station publishes a page with a tool that needs sign-off before it runs, and
then waits for approval, so one run shows two of the three waiting kinds.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_factory import Factory, Run, SQLiteStore, station
from pydantic_ai_factory.factory import Store

from app.api import create_app
from tests.support import answer, start, work


def build_factory(model: Model | str) -> Factory:
    """A publishing factory, standing in for a developer's own ``app/factory.py``."""
    publisher = Agent(model, name="publisher", instructions="Publish the page.")

    @publisher.tool_plain(requires_approval=True)
    def publish_page(path: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"published {path}"

    return Factory("publishing", [station(publisher, approval=True)])


async def render_output(run: Run, store: Store) -> str | None:
    work = run.output if run.output is not None else (run.waiting.output if run.waiting else None)
    return None if work is None else f"# What was published\n\n{work}\n"


def _publisher(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    said = [
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "publish_page"
    ]
    if not said:
        return ModelResponse(parts=[ToolCallPart("publish_page", {"path": "index.html"}, "p-1")])
    return ModelResponse(parts=[TextPart(f"The tool said: {said[-1]}")])


def _run_to_sign_off(db: Path) -> tuple[TestClient, Factory, str]:
    client = TestClient(create_app(db, build_factory=build_factory, render_output=render_output))
    factory = build_factory(FunctionModel(_publisher))
    run_id = start(client, "Publish the to-do list page.")
    work(db, factory, SQLiteStore(db))
    return client, factory, run_id


def test_your_own_factory_runs_through_the_same_api_and_worker(db: Path) -> None:
    client, factory, run_id = _run_to_sign_off(db)
    store = SQLiteStore(db)

    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["waiting"]["kind"]) == ("waiting", "tool-calls")
    [call] = run["waiting"]["sign_offs"]
    assert (call["tool"], call["args"]) == ("publish_page", {"path": "index.html"})

    answer(client, run_id, choice="reply", by="Ada", approvals={call["id"]: True})
    work(db, factory, store)
    run = client.get(f"/api/runs/{run_id}").json()
    assert (run["state"], run["waiting"]["kind"]) == ("waiting", "approval")
    assert run["output"] == "# What was published\n\nThe tool said: published index.html\n"

    answer(client, run_id, choice="approve", by="Ada")
    work(db, factory, store)
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "passed"


def test_a_tool_call_that_needs_sign_off_is_denied_through_approvals(db: Path) -> None:
    client, factory, run_id = _run_to_sign_off(db)
    [call] = client.get(f"/api/runs/{run_id}").json()["waiting"]["sign_offs"]

    unanswered = answer(client, run_id, choice="reply", by="Ada", replies={"p-9": "Yes."})
    assert unanswered.status_code == 422
    denied = answer(client, run_id, choice="reply", by="Ada", approvals={call["id"]: False})
    assert denied.status_code == 202
    work(db, factory, SQLiteStore(db))

    run = client.get(f"/api/runs/{run_id}").json()
    assert run["waiting"]["kind"] == "approval"
    assert "published index.html" not in run["output"]
    assert "denied" in run["output"]
