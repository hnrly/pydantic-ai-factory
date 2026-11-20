# Working on Pydantic AI Factory

This file tells a coding agent, or a new contributor, how to work here. The library builds AI factories on Pydantic AI. It is delivered as small stacked pull requests, each readable on its own. Read the [README](README.md) and [Contributing](docs/contributing.md) first, then the docs page for the layer you are changing.

## The shape of the code

- `src/pydantic_ai_factory/` is the generic core. It holds no software words and never imports a domain package.
- `packages/software/` is the software domain: stations such as Design. It may import the core.
- Core layers import downwards only: `methodologies`, `stores`, `factory`, `stations`, `_methodology`, `gates`, `artifacts`, `contracts`. `uv run lint-imports` checks both rules. A `cli` layer is reserved at the top but does not exist yet: the `factory` command lives in `packages/software/`.
- `_tracing` is a side module any layer may use. It depends only on the OpenTelemetry API.
- Definitions are frozen data. Running is a method that returns a serialisable record. Do not keep live state in objects or module globals.

## Pydantic AI first

Before writing infrastructure, check what Pydantic AI already provides, against the version in `uv.lock`. Do not add a second scheduler, serialiser, tool registry or retry loop.

- "Capability" means Pydantic AI's `AbstractCapability`, nothing else. Give each one a stable `id`.
- A capability instance is reused across runs. Keep per-run state out of it, or return a fresh instance from `for_run()`.
- Consider `defer_loading=True` for every capability. Eager loading is a choice to justify.
- Capabilities talk through `CapabilityEvent`, not shared objects.
- Raise `ModelRetry` when the model should try again and `ToolFailed` when the call is done and failed.
- Give every `Agent` an explicit `name=`.
- A model may produce evidence. It never decides a gate.

## Tests

- Real model requests are off by default. Use `TestModel` or `FunctionModel` through `agent.override()`.
- Name a test after the behaviour it proves, as a sentence.
- Library code does not use `assert` for control flow; raise a named error.
- A check that could not run is reported as not run. It is never counted as a pass.

## Fact sheets

Fact sheets are the files under `evals/cases/*/facts/`. They hold the
answers the benchmark hides from a system under test.
- When your task changes the factory (anything under `src/` or
  `packages/`), do not open a fact sheet.
- When your task builds or changes the benchmark (under `evals/` or its
  tests), you may read and draft the smoke and dev fact sheets.
- Never open or write `evals/cases/test/facts/` or `evals/cases/test/repo/`.
  The author writes those.
- The reference apps under `evals/reference/` hold no hidden value. Do not
  add one; a case places a value in its repository only through its
  overlay.

## Every change

1. Keep it to one idea. Do only what the pull request says.
2. Add or update the docs page for what you changed. Every code sample in the docs must run.
3. Add a line to `CHANGELOG.md` for anything a user can see.
4. Run every command under "Develop" in the README and report what each returned.
5. Run `uv run python scripts/keep_out_scan.py --require-terms` before pushing.

The reference apps under `evals/reference/` are fixtures of the benchmark. The author owns them, and they change only with a new `BENCHMARK_VERSION`.

Never merge a pull request or push to `main`. The maintainer merges.

## Writing

Write for a smart outsider. Lead with a plain summary. Use British spelling, except for the defined term "artifact", which matches the `Artifact` type. Keep one idea per sentence.
