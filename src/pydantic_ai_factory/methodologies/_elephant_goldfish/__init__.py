"""Elephant Goldfish: Google's method for design-driven development, as one capability.

The method's code is in ``_method``. Its skills, and the loader that reads them, sit beside
it in this package.
"""

from ._method import (
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
from ._skills import ExplainBack, PackageFolder, packaged_skills

__all__ = [
    "Answer",
    "ElephantGoldfish",
    "ExplainBack",
    "Finding",
    "Goldfish",
    "GoldfishFailed",
    "GoldfishReport",
    "GoldfishResult",
    "PackageFolder",
    "Severity",
    "Stage",
    "Standard",
    "packaged_skills",
]
