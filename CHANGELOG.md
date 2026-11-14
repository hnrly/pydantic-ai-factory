# Changelog

Every change a user can see is listed here, newest first.

## Unreleased

- Add agents in stations. `ctx.run_agent(agent, prompt)` runs a Pydantic AI agent, stores its `Conversation` keyed by the agent's name, and carries it on at a later attempt, sending the gate's reasons instead of the prompt. `@station` finds the agents its function refers to; `agents=` names them instead. `station(agent)` turns one agent into a station. `Factory.agents()` lists every agent before a run. `Factory.run_sync` and `Station.run_sync` run from code that is not async.
- Change stations and artifacts to take any type Pydantic can serialise, such as `str` or `list[str]`, not only models.
- Change `Rework` to take station objects: `Rework(design, limit=2)` and `Rework(build, to=design)`.
- Change the `Run` record: `output` is now the final payload, and the refs are `input_ref` and `output_ref`. `StationResult` has `input_ref`, `output_ref` and `conversations`.
- Change `StationContext` to `run_agent`, `record`, `deps`, `attempt`, `feedback` and `run_id`. `feedback` is a `Feedback` record of the reasons work came back.
- Remove the Concepts page. Each concept has its own page.

- Add factories: `Factory`, `Rework`, `Factory.run` and the `Run` record. A factory is a named, ordered list of stations; there is no separate line. Failed work can be sent back a bounded number of times with the gate's reasons; a station that did not run is never retried. A badly formed factory is refused with every finding at once. `on_step` saves progress after every attempt.
- Add gates: the `@gate` decorator, `passed`, `failed` and `needs_approval` verdicts, required evidence, `Gate.evaluate` and `GateDecision` records. A gate that cannot be evaluated is reported as such, never as a pass or a fail.
- Add stations: the `@station` decorator, `StationContext`, `Station.run` and `StationResult`, which keeps "ran" apart from "passed" and carries the traceback of a station that raised. Every definition error is a `DefinitionError` whose `findings` list each problem.
- Add artifacts: `Artifact`, `ArtifactRef` and `Producer`, with SHA-256 content digests, lineage and superseding. Add the `ArtifactStore` protocol with `MemoryStore` and `LocalStore`, which check every artifact against its digest. `Untyped` reads an artifact without its payload class, and `encode_artifact` and `decode_artifact`, in `pydantic_ai_factory.artifacts`, let you write your own store.
- Add the documentation site, with Overview, Concepts and Decisions pages, diagrams, a check that every diagram parses, and a test that runs every Python sample in the docs.
- Add a keep-out scan that stops credentials, personal paths and private terms from being published.
- Set up the repository: a uv workspace with the generic core (`pydantic-ai-factory`) and the software domain package (`pydantic-ai-factory-software`), formatting, linting, strict typing, tests, an import-rule check and CI on Python 3.11 to 3.14.
