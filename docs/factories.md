---
title: Factories
description: A factory is a named list of stations. You run a mission through it and get back a record of what happened.
---

# Factories

A factory is a named, ordered list of [stations](stations.md). You give it a mission, and each station's output becomes the next station's input. The factory holds no state of its own: running it returns a `Run`, a plain record of what happened, which is also kept in the [store](stores.md).

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
| `factory.resume(answer, store=...)` | Carries on a run that is [waiting for a person](approvals.md). The answer names its run. Async. |
| `factory.check(run, answer)` | Says why an answer does not fit a run, without running anything. |
| `factory.abandon(run, reason=..., by=..., store=...)` | Ends a run that is stuck. See [ending a run that is stuck](#ending-a-run-that-is-stuck). Async. |
| `factory.shape()` | Says what the factory looks like, as plain data. See [what a factory looks like](#what-a-factory-looks-like). |
| `run_sync`, `resume_sync`, `abandon_sync` | The same as `run`, `resume` and `abandon`, for code that is not async. |

`input` is the first station's input. A plain string becomes a [`Mission`](missions.md) when that station takes a mission or a string; a station that takes a string is given the mission as its prompt. Both calls also take `deps=`, which every station receives as `ctx.deps`.

## The run

| Field | Holds |
|---|---|
| `outcome` | `"passed"`, `"failed"`, `"did_not_run"` or `"waiting"`. A run saved between stations is `"running"`. |
| `station` | The station the work is at while the run is `"running"` or `"waiting"`, and `None` once it has ended. |
| `stopped_at` | Where a run that did not finish stopped, a waiting run included. |
| `output` | The last station's output, once the run has finished. |
| `waiting` | What a person is being asked, when the outcome is `"waiting"`. See [approvals](approvals.md). |
| `steps` | One result per station attempt, in order. |
| `answers` | Every answer a person gave, tool replies included. |
| `abandoned` | Why the run was ended, by whom and when, if [`abandon`](#ending-a-run-that-is-stuck) ended it. |
| `created_at` | When the run was first saved, in UTC. It never changes. |
| `updated_at` | When the run was last saved, in UTC. For a waiting run, that is when it started waiting. |

The factory saves `station` before each station attempt starts, so the store always says where a running run is. When work is sent back for [rework](#rework), `station` names the station it went back to. A waiting run's `station` is the station it waits at, the same as `stopped_at`.

`"failed"` means a [gate](gates.md) judged the work and it fell short. `"did_not_run"` means something could not be done at all, such as an unreachable model. The library never reports one as the other.

## Watching a run

The factory saves the run after every step. Pass `on_step=` to `run` or `resume`, and it is awaited with the run each time it is saved.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, Run, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
software = Factory("software", [station(designer), station(builder)])


async def show(run: Run) -> None:
    print(run.outcome, run.station, [step.station for step in run.steps])
    #> running designer []
    #> running builder ['designer']
    #> running builder ['designer', 'builder']
    #> passed None ['designer', 'builder']


software.run_sync("A to-do list web page.", store=MemoryStore(), on_step=show)
```

Use it to tell people a run is waiting for them, or to show progress. The run is already saved when `on_step` is called, so it only watches. The next station waits for it to return, so keep it short. If it raises, the error ends the call and the run stays saved as `"running"`, which nothing can resume, though [`abandon`](#ending-a-run-that-is-stuck) can end it. Catch your own errors inside it.

## Ending a run that is stuck

A run whose process died part-way through stays `"running"` in the store for ever. `factory.abandon` ends it, and records why:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, Run, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
software = Factory("software", [station(designer), station(builder)])
store = MemoryStore()


async def stop_before_building(run: Run) -> None:
    if run.station == "builder":
        raise RuntimeError("the worker stopped")


try:
    software.run_sync("A to-do list web page.", store=store, run_id="todo", on_step=stop_before_building)
except RuntimeError:
    pass

stuck = store.get_run_sync("todo")
print(stuck.outcome, stuck.station)
#> running builder
ended = software.abandon_sync(stuck, reason="the worker stopped", by="ops", store=store)
print(ended.outcome, ended.stopped_at, ended.station, ended.abandoned.reason)
#> did_not_run builder None the worker stopped
```

The run is saved as `"did_not_run"`, with `stopped_at` set to the station it was at and `abandoned` saying why, who ended it and when. `reason` must not be blank.

The save is a compare-and-swap, as every save is. If the stored run has moved on since you read it, `abandon` raises `RunConflict` and changes nothing. If the old process was only slow, not dead, its next save raises `RunConflict`, so nothing it does afterwards reaches the store.

Only a `"running"` run can be abandoned; any other raises `CannotAbandon`. The one exception is a waiting run made by an earlier shape of the same factory: one with the same name but another digest. Nobody can answer it here, so it may be ended too. A factory cannot abandon another factory's waiting run. To find runs that may be stuck, list the running runs that have not been saved for a while, with `store.list_runs(outcome="running", updated_before=...)`; see [listing runs](stores.md#listing-runs).

## What a factory looks like

`factory.shape()` describes a factory as plain data: its stations in order, with their versions, types, gates, agents and methodologies, and its rework rules. A web page can show the factory from it without importing the factory's code.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, Rework, station

designer = Agent("anthropic:claude-opus-5-5", name="designer")
builder = Agent("anthropic:claude-opus-5-5", name="builder")
design = station(designer)
software = Factory("software", [design, station(builder)])

shape = software.shape()
print([(s.name, s.input, s.output, s.agents) for s in shape.stations])
#> [('designer', 'str', 'str', ('designer',)), ('builder', 'str', 'str', ('builder',))]
print(shape.digest == software.digest)
#> True
```

`shape()` is built from the same data as `factory.digest`, the digest every run records, and carries it. A `FactoryShape` is a record, so `shape.model_dump_json()` gives it as JSON.

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
print(software.methodologies())
#> {'designer': (), 'builder': ()}
```

`factory.methodologies()` gives each station's [methodologies](methodologies.md), the objects themselves, before anything runs. These agents have none.

A factory is checked when it is declared. If one station's output type does not match the next station's input, or two stations share a name, `Factory(...)` raises `FactoryDefinitionError` listing every problem at once.
