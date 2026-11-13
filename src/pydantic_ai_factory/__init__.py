"""Composable, evidence-driven AI factories built on Pydantic AI.

The public contract arrives layer by layer. Each layer is exported from here as it lands.
"""

from .artifacts import (
    Artifact,
    ArtifactCorrupt,
    ArtifactInvalid,
    ArtifactNotFound,
    ArtifactRef,
    ArtifactStore,
    LocalStore,
    MemoryStore,
    Producer,
    Untyped,
)
from .contracts import DefinitionError, FactoryError, MethodologyRef
from .gates import (
    Evidence,
    Gate,
    GateDecision,
    GateDefinitionError,
    NotEvaluated,
    Verdict,
    failed,
    gate,
    needs_approval,
    passed,
)
from .stations import (
    Station,
    StationClosed,
    StationContext,
    StationDefinitionError,
    StationInputInvalid,
    StationResult,
    station,
)

__version__ = "0.1.0"

__all__ = [
    "Artifact",
    "ArtifactCorrupt",
    "ArtifactInvalid",
    "ArtifactNotFound",
    "ArtifactRef",
    "ArtifactStore",
    "DefinitionError",
    "Evidence",
    "FactoryError",
    "Gate",
    "GateDecision",
    "GateDefinitionError",
    "LocalStore",
    "MemoryStore",
    "MethodologyRef",
    "NotEvaluated",
    "Producer",
    "Station",
    "StationClosed",
    "StationContext",
    "StationDefinitionError",
    "StationInputInvalid",
    "StationResult",
    "Untyped",
    "Verdict",
    "__version__",
    "failed",
    "gate",
    "needs_approval",
    "passed",
    "station",
]
