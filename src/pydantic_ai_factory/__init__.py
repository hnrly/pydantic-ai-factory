"""Composable, evidence-driven AI factories built on Pydantic AI.

The public contract arrives layer by layer. Each layer is exported from here as it lands.
"""

from .artifacts import (
    Artifact,
    ArtifactCorrupt,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStore,
    LocalStore,
    MemoryStore,
    Producer,
)
from .contracts import FactoryError, MethodologyRef

__version__ = "0.1.0"

__all__ = [
    "Artifact",
    "ArtifactCorrupt",
    "ArtifactNotFound",
    "ArtifactRef",
    "ArtifactStore",
    "FactoryError",
    "LocalStore",
    "MemoryStore",
    "MethodologyRef",
    "Producer",
    "__version__",
]
