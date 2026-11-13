# Changelog

Every change a user can see is listed here, newest first.

## Unreleased

- Add lines: `Line`, `Rework`, `run_line` and the `LineRun` record. Failed work can be sent back a bounded number of times with the gate's reasons; a station that did not run is never retried. A badly formed line is refused with every finding at once.
- Add gates: the `@gate` decorator, `passed`, `failed` and `needs_approval` verdicts, required evidence, and `GateDecision` records. A gate that cannot be evaluated is reported as such, never as a pass or a fail.
- Add stations: the `@station` decorator, `StationContext`, `run_station` and `StationResult`, which keeps "ran" apart from "passed".
- Add artifacts: `Artifact`, `ArtifactRef` and `Producer`, with SHA-256 content digests, lineage and superseding. Add the `ArtifactStore` protocol with `MemoryStore` and `LocalStore`, which check every artifact against its digest. `Untyped` reads an artifact without its payload class, and `encode_artifact` and `decode_artifact` let you write your own store.
- Add the documentation site, with Overview, Concepts and Decisions pages, diagrams, and a test that runs every Python sample in the docs.
- Add a keep-out scan that stops credentials, personal paths and private terms from being published.
- Set up the repository: a uv workspace with the generic core (`pydantic-ai-factory`) and the software domain package (`pydantic-ai-factory-software`), formatting, linting, strict typing, tests, an import-rule check and CI on Python 3.11 to 3.14.
