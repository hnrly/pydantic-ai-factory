"""Stores: where a factory keeps its artifacts and its runs.

Three stores keep both: ``MemoryStore`` in memory, ``LocalStore`` in a folder and
``SQLiteStore`` in one SQLite file. To write your own, implement the async
methods of ``ArtifactStore`` and ``RunStore``; ``encode_artifact``, ``decode_artifact``,
``encode_run`` and ``decode_run`` do the integrity checks.
"""

from ..artifacts import ArtifactNotFound, ArtifactStore, decode_artifact, encode_artifact
from ..factory import RunConflict, RunNotFound, RunStore, Store
from ._stores import (
    LocalStore,
    MemoryStore,
    SQLiteStore,
    StoreBusy,
    StoreSchemaMismatch,
    decode_run,
    encode_run,
)

__all__ = [
    "ArtifactNotFound",
    "ArtifactStore",
    "LocalStore",
    "MemoryStore",
    "RunConflict",
    "RunNotFound",
    "RunStore",
    "SQLiteStore",
    "Store",
    "StoreBusy",
    "StoreSchemaMismatch",
    "decode_artifact",
    "decode_run",
    "encode_artifact",
    "encode_run",
]
