# Pydantic AI Factory

Pydantic AI Factory is a Python library for building AI factories on [Pydantic AI](https://pydantic.dev/docs/ai/). A factory is a repeatable production system: it takes a mission, moves the work through stations, and proves each result with evidence before a person approves it. Your agents stay ordinary Pydantic AI agents. The library is being built in public, one small pull request at a time. The first factory, for software, has one working station: Design.

## Try it

```bash
git clone https://github.com/hnrly/pydantic_ai_factory
cd pydantic_ai_factory
uv sync
uv run factory run "A to-do list web page where I can add tasks and tick them off." --offline
```

That drafts a design for a to-do list web page, has fresh reviewers check it, and asks you to approve it. It needs no model key. In Python it is four lines:

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory

software = SoftwareFactory("anthropic:claude-opus-5-5")
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=MemoryStore())
```

[Getting started](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/getting-started.md) builds a factory of your own; [the software factory](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/software-factory.md) explains what this command did.

> **Status: pre-alpha.** Nothing is published to PyPI yet. The public contract lands layer by layer and may change until the first release.

## Why a factory

An agent finishing a task is not the same as the work being right. A factory separates the two questions. Did the station run? Did its output pass a gate that reads real evidence? That split, and the records behind it, are what this library adds on top of Pydantic AI's agents and capabilities.

| Idea | What it means here |
|---|---|
| Mission | What the factory commits to, with measurable success criteria. A string becomes one. |
| Factory | A named, ordered list of stations, with bounded rework. |
| Station | One bounded job with typed input and output. An agent is optional. |
| Gate | Plain code that judges recorded evidence. A model never decides a gate. |
| Artifact | An unchangeable, content-addressed record of what a station handed on. |
| Methodology | A Pydantic AI capability with a name, a version, the evidence it records and a gate that judges it. |

"Capability" always means Pydantic AI's own `AbstractCapability`. The library adds no second meaning.

## What is here today

- [Getting started](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/getting-started.md): build a factory of your own, with a design station a person approves and a build station that hands on its change as a diff and a review.
- [Factories and rework](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/factories.md): join stations into a factory, send failed work back a bounded number of times, and read the record of the run.
- [Agents in stations](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/agents.md): run Pydantic AI agents inside a station with `ctx.run_agent`, which keeps each agent's conversation and carries it on when work comes back.
- [Stations](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/stations.md) and [gates](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/gates.md): declare a typed station, run it once, and get a result that keeps "ran" apart from "passed".
- [Approvals](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/approvals.md): a run waits for a person to approve work, settle an escalation or answer an agent's question, and carries on in any process.
- [Stores](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/stores.md): unchangeable, content-addressed artifacts with lineage, and every run, kept in memory or in a folder.
- [Missions and signals](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/missions.md): commit to a mission, or turn signals such as a bug report into one, and get each run's outcome back as a signal.
- [Methodologies](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/methodologies.md): attach a named, versioned way of working to an agent as a capability; inside a station its evidence is stored, its gate is joined to the station's and its name is stamped on every record.
- [Elephant Goldfish](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/elephant-goldfish.md), the methodology that ships: fresh goldfish review the author's draft inside its own run, the author revises in the same conversation, and anything contested or still open goes to a person.
- [Observability](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/observability.md): every run leaves an OpenTelemetry trace, with a span for the run, each station attempt, each agent run, each gate and each answer a person gave, viewable in Logfire or any other backend.
- [The software factory](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/software-factory.md) and its first station, [Design](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/design-station.md): `SoftwareFactory(model)` turns a mission into a design that fresh goldfish have reviewed and a person approves. The `factory` command runs it from a terminal, with `--offline` for a scripted model and no key. Build, Quality and Release are coming soon.
- Two distributions in one workspace: `pydantic-ai-factory` (the generic core) and `pydantic-ai-factory-software` (software stations). The core never imports a domain package.
- Formatting, linting, strict typing, tests and an import-rule check, run in CI on Python 3.11 to 3.14. CI also runs the installed `factory` command offline. A `live` test marker runs the getting-started path against a real model by hand before each release.
- A documentation site in `docs/` and `site/`. Start with [Factories](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/factories.md) and [Decisions](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/decisions.md).
- A keep-out scan that stops credentials, personal paths and private terms from being published.

## Develop

You need [uv](https://docs.astral.sh/uv/) and Python 3.11 or later.

```bash
uv sync
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run lint-imports
uv run pytest
uv run python scripts/keep_out_scan.py
npm --prefix site ci && npm --prefix site run build   # the docs site; needs Node 22
```

See [Contributing](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/contributing.md) for how changes are shaped and reviewed.

## Licence

MIT. This project is independent and is not affiliated with Pydantic.
