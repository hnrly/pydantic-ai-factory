"""The methodologies this library ships.

Elephant Goldfish is Google's method for design-driven development: the author keeps the
whole conversation, and fresh reviewers, the goldfish, see only the finished work.
"""

from ._elephant_goldfish import (
    Answer,
    ElephantGoldfish,
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
    "Finding",
    "Goldfish",
    "GoldfishFailed",
    "GoldfishReport",
    "GoldfishResult",
    "Severity",
    "Stage",
    "Standard",
]
