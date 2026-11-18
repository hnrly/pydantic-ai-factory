"""The software factory for Pydantic AI Factory.

``SoftwareFactory(model)`` turns a mission into a reviewed design that a person approves.
Its first station is Design. Build, Quality and Release follow.
"""

from ._design import DESIGN_GOLDFISH, DESIGN_STANDARDS, Design, render_markdown
from ._factory import SoftwareFactory
from ._offline import offline_model

__version__ = "0.1.1"

__all__ = [
    "DESIGN_GOLDFISH",
    "DESIGN_STANDARDS",
    "Design",
    "SoftwareFactory",
    "__version__",
    "offline_model",
    "render_markdown",
]
