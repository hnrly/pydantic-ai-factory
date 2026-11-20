"""The software factory for Pydantic AI Factory.

``SoftwareFactory(model)`` turns a mission into a reviewed design that a person approves.
Its first station is Design. Build, Quality and Release follow.
"""

from ._design import (
    DESIGN_GOLDFISH,
    DESIGN_STANDARDS,
    Alternative,
    Change,
    Check,
    Decision,
    Design,
    Journey,
    Risk,
    Step,
    design_is_buildable,
    render_markdown,
)
from ._factory import SoftwareFactory
from ._offline import offline_model

__version__ = "0.1.1"

__all__ = [
    "DESIGN_GOLDFISH",
    "DESIGN_STANDARDS",
    "Alternative",
    "Change",
    "Check",
    "Decision",
    "Design",
    "Journey",
    "Risk",
    "SoftwareFactory",
    "Step",
    "__version__",
    "design_is_buildable",
    "offline_model",
    "render_markdown",
]
