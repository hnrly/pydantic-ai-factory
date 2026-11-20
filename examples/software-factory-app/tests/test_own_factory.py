"""A factory of your own, put in place of ``SoftwareFactory``, is served the same way.

Its one station publishes a page with a tool that needs sign-off before it runs, and
then waits for approval, so one run shows two of the three waiting kinds.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
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
from pydantic_ai_factory import Factory, SQLiteStore, station
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, work

from tests.support import answer, column, run, start


def build_factory(model: Model | str) -> Factory:
    """A publishing factory, standing in for a developer's own."""
    publisher = Agent(model, name="publisher", instructions="Publish the page.")

    @publisher.tool_plain(requires_approval=True)
    def publish_page(path: str) -> str:  # pyright: ignore[reportUnusedFunction]
        return f"published {path}"

    return Factory("publishing", [station(publisher, approval=True)])


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


class Publishing:
    def __init__(self, db: Path, client: TestClient) -> None:
        self.store = SQLiteStore(db)
        self.dispatcher = SQLiteDispatcher(db)
        self.client = client

    def work(self) -> None:
        model = FunctionModel(_publisher)
        work(build_factory, self.store, self.dispatcher, model=model, until_idle=True)


@pytest.fixture
def publishing(tmp_path: Path) -> Iterator[Publishing]:
    db = tmp_path / "factory.db"
    app = board_app(build_factory, SQLiteStore(db), SQLiteDispatcher(db))
    with TestClient(app) as client:
        yield Publishing(db, client)


def test_your_own_factory_is_served_by_the_same_board_and_worker(publishing: Publishing) -> None:
    client = publishing.client
    run_id = start(client, "Publish the to-do list page.")
    publishing.work()

    signing = run(client, run_id)
    assert (signing["state"], signing["wait"]["kind"]) == ("waiting", "tool-calls")
    [call] = signing["wait"]["sign_offs"]
    assert (call["tool"], call["args"]) == ("publish_page", {"path": "index.html"})

    assert answer(client, run_id, "reply", approvals={call["id"]: True}) == 202
    publishing.work()
    approving = run(client, run_id)
    assert (approving["state"], approving["wait"]["kind"]) == ("waiting", "approval")
    assert approving["output"] == "The tool said: published index.html"
    assert approving["output_format"] == "text"

    assert answer(client, run_id, "approve") == 202
    publishing.work()
    assert column(client, run_id) == "done"


def test_a_tool_call_that_needs_sign_off_is_denied_through_approvals(
    publishing: Publishing,
) -> None:
    client = publishing.client
    run_id = start(client, "Publish the to-do list page.")
    publishing.work()
    [call] = run(client, run_id)["wait"]["sign_offs"]

    assert answer(client, run_id, "reply", replies={"p-9": "Yes."}) == 422
    assert answer(client, run_id, "reply", approvals={call["id"]: False}) == 202
    publishing.work()

    denied = run(client, run_id)
    assert denied["wait"]["kind"] == "approval"
    assert "published index.html" not in denied["output"]
    assert "denied" in denied["output"]
