---
title: Stations
description: A station is one bounded job with a typed input and output. It may run agents, or none.
---

# Stations

A station is one bounded job in a factory, with a typed input and a typed output. It may run one agent, several, or none at all. Each attempt answers two questions separately: did it run, and did its output pass?

The shortest station is an agent:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")

design = station(designer)
result = design.run_sync("A to-do list web page where I can add tasks and tick them off.", store=MemoryStore())
print(design.name, result.outcome)
#> designer not_judged
```

`station(agent)` passes its input to the agent as the prompt and hands on the agent's output. It takes the same options as the decorator below, and its name is the agent's `name`.

## Stations as functions

When a station runs more than one agent, or needs code around one, write it as an async function.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, StationContext, station

builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
reviewer = Agent("anthropic:claude-opus-5-5", name="reviewer", instructions="Say what is wrong, if anything.")


@station
async def build(ctx: StationContext, design: str) -> str:
    await ctx.run_agent(builder, design)
    review = await ctx.run_agent(reviewer, design)
    return review.output


software = Factory("software", [build])
run = software.run_sync("One page, index.html, with a box to add tasks.", store=MemoryStore())
print(run.output)
#> The page matches the design. Nothing is wrong.
```

The input and output types come from the annotations. Each may be any type Pydantic can serialise: a `str`, a list, a dataclass or a model. [Agents in stations](agents.md) explains `ctx.run_agent`.

A station with no agent is just as welcome. A station that formats a design, or runs a test suite, is ordinary Python.

## Did it run? Did it pass?

| Outcome | Means |
|---|---|
| `passed` | A [gate](gates.md) judged the output and passed it. |
| `failed` | A gate judged the output and failed it. |
| `needs_approval` | A gate is satisfied, and a person must decide. |
| `not_judged` | The station has no gate, so nothing judged the output. |
| `did_not_run` | The station raised, or its gate had no evidence to read. |

Only a gate can fail a station. A station that raises has not failed: it did not run, and the exception is kept on the result. This matters because failed work can be sent back for [rework](factories.md#rework), and redoing the work does not fix an unreachable model.

## Recording evidence

A station can record more than its output, for a gate to read. Record each piece with `ctx.record`.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact, Evidence, MemoryStore, StationContext, Verdict, failed, gate, passed, station


class TestRun(BaseModel):
    passed: int
    failed: int


@gate(version="1", requires=[TestRun])
def tests_pass(output: Artifact[str], evidence: Evidence) -> Verdict:
    run = evidence.one(TestRun).payload
    return passed("all tests pass") if run.failed == 0 else failed(f"{run.failed} tests failed")


@station(gate=tests_pass)
async def build(ctx: StationContext, design: str) -> str:
    await ctx.record(TestRun(passed=12, failed=0))
    return "Built index.html."


print(build.run_sync("One page, index.html.", store=MemoryStore()).outcome)
#> passed
```

If the gate needs evidence the station did not record, the gate is not evaluated and the attempt did not run. A check that never happened is never counted as a pass.

## Options

| Option | Default | Means |
|---|---|---|
| `gate` | none | The [gate](gates.md) that judges the output. |
| `agents` | inferred | The agents the station runs. See [which agents](agents.md#which-agents-a-station-runs). |
| `name` | the function's name | The station's name in every record. |
| `version` | `"0"` | Change it when the station's behaviour changes. |

`Station.run_sync(input, store=...)` runs one attempt on its own, outside a factory. That is how a station is tested.
