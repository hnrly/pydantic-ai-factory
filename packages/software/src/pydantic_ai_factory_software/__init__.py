"""The software factory for Pydantic AI Factory.

``SoftwareFactory(model)`` turns a mission into a reviewed design that a person approves.
Its first station is Design, which ``design_station`` builds for a factory of your own.
Build, Quality and Release follow.
"""

from ._design import (
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
from ._station import (
    DESIGN_GOLDFISH,
    DESIGN_STANDARDS,
    DESIGNER_INSTRUCTIONS,
    design_review,
    design_station,
)

__version__ = "0.1.1"

__all__ = [
    "DESIGNER_INSTRUCTIONS",
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
    "design_review",
    "design_station",
    "render_markdown",
]
