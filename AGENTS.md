# Working on Pydantic AI Factory

This file tells a coding agent, or a new contributor, how to work here. The library builds AI factories on Pydantic AI. It is delivered as small stacked pull requests, each readable on its own. Read the [README](README.md) and [Contributing](docs/contributing.md) first, then the docs page for the layer you are changing.

## The shape of the code

- `src/pydantic_ai_factory/` is the generic core. It holds no software words and never imports a domain package. Anyone should be able to build their own factory from the core and Pydantic AI alone, such as a software factory of their own, without the opinionated `packages/software/`.
- `packages/software/` is the software domain: stations such as Design. It may import the core.
- Core layers import downwards only: `serve`, `methodologies`, `stores`, `factory`, `stations`, `_methodology`, `gates`, `artifacts`, `contracts`. `uv run lint-imports` checks both rules. `serve` sits at the top, and nothing in the core imports it; it uses only the standard library and the core. A `cli` layer is reserved below `serve` but does not exist yet: the `factory` command lives in `packages/software/`.
- `_tracing` is a side module any layer may use. It depends only on the OpenTelemetry API.
- Definitions are frozen data. Running is a method that returns a serialisable record. Do not keep live state in objects or module globals.

## Pydantic AI first

Before writing infrastructure, check what Pydantic AI already provides, against the version in `uv.lock`. Do not add a second scheduler, serialiser, tool registry or retry loop.

Pydantic AI Harness is a core dependency, and any core layer may import it. Harness extras that bring heavy dependencies, such as Coder or a sandbox, are optional extras of the core. The core must import and run without them.

### Check the harness before every feature

For every feature you build, check whether Pydantic AI or the harness already provides it, before you design it. Read the documentation for the versions in `uv.lock`:

1. The `pydantic-ai-harness` and `building-pydantic-ai-agents` skills, when your agent has them installed. They list the harness capabilities and how to use them.
2. The installed packages. Each harness capability has a `README.md` in `.venv/lib/python*/site-packages/pydantic_ai_harness/<capability>/`. Pydantic AI's own capabilities are in `pydantic_ai/capabilities/`.
3. The documentation sites, [Pydantic AI](https://pydantic.dev/docs/ai/) and [Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/), through Context7 or the web.

Then:

- Use the existing capability when one fits, such as `AskUser` for asking a person or `Skills` for Agent Skills. Wrap it thinly only for what it lacks.
- If you build your own, say why the existing one does not fit, in the pull request and on the Decisions page.
- Do not import a harness module whose name starts with `_`. Use a public API, or write the small piece yourself.
- In the pull request, list what you checked and what you found, under "How it was proven".

- "Capability" means Pydantic AI's `AbstractCapability`, nothing else. Give each one a stable `id`.
- A capability instance is reused across runs. Keep per-run state out of it, or return a fresh instance from `for_run()`.
- Consider `defer_loading=True` for every capability. Eager loading is a choice to justify.
- Capabilities talk through `CapabilityEvent`, not shared objects.
- Raise `ModelRetry` when the model should try again and `ToolFailed` when the call is done and failed.
- Give every `Agent` an explicit `name=`.
- A model may produce evidence. It never decides a gate.

## Before 1.0

Until version 1.0, choose the best design over compatibility.

- Change public APIs and stored formats when that gives the better shape. Do not add shims, aliases, deprecation periods or code that converts old data.
- Say what broke, and what to do about it, in `CHANGELOG.md`.
- A store that finds data in an older shape refuses it with a named error, such as `StoreSchemaMismatch`. It never reads that data wrongly or silently.

## Tests

- Real model requests are off by default. Use `TestModel` or `FunctionModel` through `agent.override()`.
- Name a test after the behaviour it proves, as a sentence.
- Library code does not use `assert` for control flow; raise a named error.
- A check that could not run is reported as not run. It is never counted as a pass.

## Fact sheets

Fact sheets are the files under `evals/*/cases/*/facts/`. They hold the
answers the benchmark hides from a system under test.
- When your task changes the factory (anything under `src/` or
  `packages/`), do not open a fact sheet.
- When your task builds or changes the benchmark (under `evals/` or its
  tests), you may read and draft the smoke and dev fact sheets.
- Never open or write `evals/*/cases/test/facts/` or `evals/*/cases/test/repo/`.
  The author writes those.

## Every change

1. Keep it to one idea. Do only what the pull request says.
2. Add or update the docs page for what you changed. Every code sample in the docs must run.
3. Add a line to `CHANGELOG.md` for anything a user can see.
4. Run every command under "Develop" in the README and report what each returned.
5. Run `uv run python scripts/keep_out_scan.py --require-terms` before pushing.

Never merge a pull request or push to `main`. The maintainer merges.

## Writing

Write for a smart outsider. Lead with a plain summary. Use British spelling, except for the defined term "artifact", which matches the `Artifact` type. Keep one idea per sentence.
