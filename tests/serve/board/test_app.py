"""The board app serves only a factory it can start, and needs no model key."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import (
    Factory,
    SQLiteStore,
    StationContext,
    station,
)
from pydantic_ai_factory.serve import BoardUnsupported, SQLiteDispatcher, board_app

from .support import (
    built,
)

# Building the app.


def test_a_factory_whose_first_station_takes_no_mission_is_refused(
    store: SQLiteStore, jobs: SQLiteDispatcher
) -> None:
    @station
    async def count(ctx: StationContext, number: int) -> str:
        return str(number)

    with pytest.raises(BoardUnsupported, match="count"):
        board_app(built(Factory("numbers", [count])), store, jobs)


def test_without_a_built_interface_the_root_names_the_build_command(
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr("pydantic_ai_factory.serve._board.app.STATIC", tmp_path / "not-built")
    with caplog.at_level(logging.WARNING, logger="pydantic_ai_factory.serve"):
        app = board_app(built(factory), store, jobs)

    with TestClient(app) as client:
        page = client.get("/")

    assert page.status_code == 200
    assert "npm --prefix web/board ci &amp;&amp; npm --prefix web/board run build" in page.text
    assert "npm --prefix web/board" in caplog.text


def test_the_web_process_serves_the_board_with_no_model_key(
    store: SQLiteStore, jobs: SQLiteDispatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pydantic_ai_factory_software import SoftwareFactory

    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    given: list[Model | str] = []

    def build(model: Model | str) -> Factory:
        given.append(model)
        return SoftwareFactory(model)

    with TestClient(board_app(build, store, jobs)) as client:
        response = client.get("/api/board")

    assert response.status_code == 200
    assert [station["name"] for station in response.json()["shape"]["stations"]] == ["design"]
    assert [type(model) for model in given] == [TestModel]


def test_without_the_web_extra_board_app_says_to_install_it(
    factory: Factory,
    store: SQLiteStore,
    jobs: SQLiteDispatcher,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pydantic_ai_factory.serve as serve_module

    monkeypatch.setitem(sys.modules, "fastapi", None)
    for name in [
        name for name in sys.modules if name.startswith("pydantic_ai_factory.serve._board")
    ]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.delattr(serve_module, "_board")

    with pytest.raises(ImportError, match=r"pydantic-ai-factory\[web\]"):
        board_app(built(factory), store, jobs)
