---
title: Factories
description: A factory is a named list of stations. You run work through it and get back a record of what happened.
---

# Factories

A factory is a named, ordered list of [stations](stations.md). You give it work, and each station's output becomes the next station's input. The factory holds no state of its own: running it returns a `Run`, a plain record of what happened, which is also kept in the [store](stores.md).

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")

software = Factory("software", [station(designer), station(builder)])

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=MemoryStore())
print(run.outcome)
#> passed
print([step.station for step in run.steps])
#> ['designer', 'builder']
```

Name a factory for what it makes, such as `"software"`, not for one piece of work it is given.

## Running

| Call | Does |
|---|---|
| `factory.run(input, store=...)` | Runs the work and returns a `Run`. Async. |
| `factory.resume(run, answer, store=...)` | Carries on a run that is [waiting for a person](approvals.md). Async. |
| `run_sync`, `resume_sync` | The same, for code that is not async. |

`input` is the first station's input. Both calls also take `deps=`, which every station receives as `ctx.deps`.

## The run

| Field | Holds |
|---|---|
| `outcome` | `"passed"`, `"failed"`, `"did_not_run"` or `"waiting"`. |
| `output` | The last station's output, once the run has finished. |
| `waiting` | What a person is being asked, when the outcome is `"waiting"`. See [approvals](approvals.md). |
| `steps` | One result per station attempt, in order. |
| `approvals` | Every decision a person made. |

`"failed"` means a [gate](gates.md) judged the work and it fell short. `"did_not_run"` means something could not be done at all, such as an unreachable model. The library never reports one as the other.

## Rework

When a station fails its gate, a rework rule sends the work back a fixed number of times.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Artifact, Evidence, Factory, MemoryStore, Rework, Verdict, failed, gate, passed, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")


@gate(version="1")
def mentions_blank_tasks(output: Artifact[str], evidence: Evidence) -> Verdict:
    if "blank" in output.payload.lower():
        return passed("the design says what happens to a blank task")
    return failed("the design never says what happens to a blank task")


design = station(designer, gate=mentions_blank_tasks)
software = Factory("software", [design], rework=[Rework(design, limit=2)])
run = software.run_sync("A to-do list web page.", store=MemoryStore())
print(run.outcome, len(run.steps))
#> passed 2
```

`Rework(design)` redoes `design` when it fails. To send work back to an earlier station, add `to=`: `Rework(build, to=design)` redoes the design when the build fails. The agent picks up its own conversation and is told what was wrong; see [agents in stations](agents.md#later-attempts). At the limit the run stops as `"failed"`. Pass `on_limit="continue"` to carry on anyway, or `on_limit="escalate"` to ask a person.

Rework is only for work that was judged and failed. A station that did not run is never sent back.

## Before a run

A factory can say what it will use before anything runs, so you can check keys and costs first.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, station

designer = Agent("anthropic:claude-opus-5-5", name="designer")
builder = Agent("anthropic:claude-opus-5-5", name="builder")
software = Factory("software", [station(designer), station(builder)])

print({name: [agent.name for agent in agents] for name, agents in software.agents().items()})
#> {'designer': ['designer'], 'builder': ['builder']}
```

A factory is checked when it is declared. If one station's output type does not match the next station's input, or two stations share a name, `Factory(...)` raises `FactoryDefinitionError` listing every problem at once.
