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
    Producer,
    Untyped,
)
from .contracts import DefinitionError, FactoryError, MethodologyRef
from .factory import (
    ApprovalRefused,
    Decision,
    Factory,
    FactoryDefinitionError,
    Rework,
    Run,
    RunConflict,
    RunNotFound,
    RunStore,
)
from .gates import (
    Evidence,
    Gate,
    GateDecision,
    GateDefinitionError,
    NotEvaluated,
    Verdict,
    escalate,
    failed,
    gate,
    passed,
)
from .stations import (
    AskPerson,
    Escalate,
    Feedback,
    Station,
    StationClosed,
    StationContext,
    StationDefinitionError,
    StationInputInvalid,
    StationResult,
    Waiting,
    station,
)
from .stores import LocalStore, MemoryStore

__version__ = "0.1.0"

__all__ = [
    "ApprovalRefused",
    "Artifact",
    "ArtifactCorrupt",
    "ArtifactInvalid",
    "ArtifactNotFound",
    "ArtifactRef",
    "ArtifactStore",
    "AskPerson",
    "Decision",
    "DefinitionError",
    "Escalate",
    "Evidence",
    "Factory",
    "FactoryDefinitionError",
    "FactoryError",
    "Feedback",
    "Gate",
    "GateDecision",
    "GateDefinitionError",
    "LocalStore",
    "MemoryStore",
    "MethodologyRef",
    "NotEvaluated",
    "Producer",
    "Rework",
    "Run",
    "RunConflict",
    "RunNotFound",
    "RunStore",
    "Station",
    "StationClosed",
    "StationContext",
    "StationDefinitionError",
    "StationInputInvalid",
    "StationResult",
    "Untyped",
    "Verdict",
    "Waiting",
    "__version__",
    "escalate",
    "failed",
    "gate",
    "passed",
    "station",
]
