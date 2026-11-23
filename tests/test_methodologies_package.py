"""Elephant Goldfish moved into a private subpackage; its names import from where they did."""

from __future__ import annotations

import pydantic_ai_factory.methodologies as methodologies

# Every name `pydantic_ai_factory.methodologies` exported in 0.1.
PUBLIC_IN_0_1 = (
    "Answer",
    "ElephantGoldfish",
    "Finding",
    "Goldfish",
    "GoldfishFailed",
    "GoldfishReport",
    "GoldfishResult",
    "Severity",
    "Stage",
    "Standard",
)


def test_public_names_are_unchanged() -> None:
    for name in PUBLIC_IN_0_1:
        assert getattr(methodologies, name) is not None, name
    assert set(PUBLIC_IN_0_1) <= set(methodologies.__all__)
