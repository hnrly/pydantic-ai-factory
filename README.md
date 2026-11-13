# Pydantic AI Factory

Pydantic AI Factory is a Python library for building AI factories on [Pydantic AI](https://pydantic.dev/docs/ai/). A factory is a repeatable production system: it takes a signal, commits to a mission, moves work through stations, and proves each result with evidence before a person approves it. The library is being built in public, one small pull request at a time. Today it holds the repository foundations only; the first runnable station, Design, arrives at the end of the first delivery.

> **Status: pre-alpha.** Nothing is published to PyPI yet. The public contract lands layer by layer and may change until the first release.

## Why a factory

An agent finishing a task is not the same as the work being right. A factory separates the two questions. Did the station run? Did its output pass a gate that reads real evidence? That split, and the records behind it, are what this library adds on top of Pydantic AI's agents and capabilities.

| Idea | What it means here |
|---|---|
| Signal | The one typed way in: a piece of data from a person or a system. |
| Mission | What the factory commits to, with measurable success criteria. |
| Line | An ordered route through stations, with bounded rework. |
| Station | One bounded job with typed input and output. An agent is optional. |
| Gate | Plain code that judges recorded evidence. A model never decides a gate. |
| Artifact | An unchangeable, content-addressed record of what a station handed on. |
| Methodology | A versioned way of working: what agents are given, what evidence is required, how it is judged. |

"Capability" always means Pydantic AI's own `AbstractCapability`. The library adds no second meaning.

## What is here today

- Two distributions in one workspace: `pydantic-ai-factory` (the generic core) and `pydantic-ai-factory-software` (software stations). The core never imports a domain package.
- Formatting, linting, strict typing, tests and an import-rule check, run in CI on Python 3.11 to 3.14.
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
```

See [Contributing](https://github.com/hnrly/pydantic_ai_factory/blob/main/docs/contributing.md) for how changes are shaped and reviewed.

## Licence

MIT. This project is independent and is not affiliated with Pydantic.
