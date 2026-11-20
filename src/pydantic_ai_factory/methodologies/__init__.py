"""The methodologies this library ships.

Elephant Goldfish is Google's method for design-driven development: the author keeps the
whole conversation, and fresh reviewers, the goldfish, see only the finished work.

This package needs the ``methodologies`` extra, which brings Pydantic AI Harness:
``pip install "pydantic-ai-factory[methodologies]"``. The rest of the core does not.
"""

import importlib

try:
    importlib.import_module("pydantic_ai_harness")
except ModuleNotFoundError as error:
    if error.name != "pydantic_ai_harness":
        raise
    raise ImportError(
        "Elephant Goldfish needs the methodologies extra: "
        'pip install "pydantic-ai-factory[methodologies]"'
    ) from None

from ._elephant_goldfish import (
    Answer,
    ElephantGoldfish,
    ExplainBack,
    Finding,
    Goldfish,
    GoldfishFailed,
    GoldfishReport,
    GoldfishResult,
    Severity,
    Stage,
    Standard,
)

__all__ = [
    "Answer",
    "ElephantGoldfish",
    "ExplainBack",
    "Finding",
    "Goldfish",
    "GoldfishFailed",
    "GoldfishReport",
    "GoldfishResult",
    "Severity",
    "Stage",
    "Standard",
]
