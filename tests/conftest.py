"""Shared test set-up."""

from __future__ import annotations

import pytest
from pydantic_ai import models

# No test may call a real model. A test that needs a model overrides it with a test double.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio only."""
    return "asyncio"
