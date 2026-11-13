# Changelog

Every change a user can see is listed here, newest first.

## Unreleased

- Add gates: the `@gate` decorator, `passed`, `failed` and `needs_approval` verdicts, required evidence, and `GateDecision` records. A gate that cannot be evaluated is reported as such, never as a pass or a fail.
- Add stations: the `@station` decorator, `StationContext`, `run_station` and `StationResult`, which keeps "ran" apart from "passed" and carries the traceback of a station that raised.
- Add artifacts: `Artifact`, `ArtifactRef` and `Producer`, with SHA-256 content digests, lineage and superseding. Add the `ArtifactStore` protocol with `MemoryStore` and `LocalStore`, which check every artifact against its digest. `Untyped` reads an artifact without its payload class, and `encode_artifact` and `decode_artifact`, in `pydantic_ai_factory.artifacts`, let you write your own store.
- Add the documentation site, with Overview, Concepts and Decisions pages, diagrams, a check that every diagram parses, and a test that runs every Python sample in the docs.
- Add a keep-out scan that stops credentials, personal paths and private terms from being published.
- Set up the repository: a uv workspace with the generic core (`pydantic-ai-factory`) and the software domain package (`pydantic-ai-factory-software`), formatting, linting, strict typing, tests, an import-rule check and CI on Python 3.11 to 3.14.
