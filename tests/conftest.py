"""Shared test set-up."""

from __future__ import annotations

import asyncio
import os
import warnings
from collections.abc import Iterator

import pytest
from pydantic_ai import models

# No test may call a real model. A test that needs a model overrides it with a test double.
models.ALLOW_MODEL_REQUESTS = False
# Pydantic AI prints a banner on an agent's first run; keep test output clean.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip tests marked ``live`` unless they were asked for with ``-m live``."""
    if "live" in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason="makes real, paid model requests; select it with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _close_the_sync_event_loop(request: pytest.FixtureRequest) -> Iterator[None]:
    """Close the event loop a ``run_sync`` call left behind, so none leaks between tests.

    ``run_sync`` keeps the thread's loop open, as Pydantic AI's does, so model clients
    survive between calls. Async tests run on anyio's own loop and are left alone.
    """
    yield
    if "anyio_backend" in request.fixturenames:
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
    if not loop.is_running():
        loop.close()
        asyncio.set_event_loop(None)
