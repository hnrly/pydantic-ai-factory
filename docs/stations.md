---
title: Stations
description: How to declare a station, run it once and read what happened.
---

# Stations

A station is one bounded job with a typed input and a typed output. It is the unit a factory is built from. A station may use one agent, several agents or none: the first example below uses no model at all. This page shows how to declare a station, run it, and read a result that keeps "did it run" apart from "did it pass".

## Your first station

A station is an async function with a decorator. It takes a context and one input, and returns one output. The input and the output must each be a Pydantic model; the decorator reads their types from the annotations.

This station counts words. Its [gate](gates.md) passes short texts and fails long ones.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    MemoryStore,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    run_station,
    station,
)


class Text(BaseModel):
    body: str


class Count(BaseModel):
    words: int


@gate(version="1")
def under_ten_words(output: Artifact[Count], evidence: Evidence) -> Verdict:
    if output.payload.words >= 10:
        return failed(f"{output.payload.words} words is not under 10")
    return passed("short enough")


@station(gate=under_ten_words)
async def count_words(ctx: StationContext, text: Text) -> Count:
    return Count(words=len(text.body.split()))


async def main() -> None:
    store = MemoryStore()
    result = await run_station(count_words, Text(body="one two three"), store=store)

    print(result.outcome, result.ran, result.passed)
    #> passed True True

    assert result.output is not None
    output = await store.get(result.output, Count)
    print(output.payload)
    #> words=3


asyncio.run(main())
```

Three things happened. The station ran. Its output was stored as an [artifact](artifacts.md). The gate judged that artifact and passed it.

## Reading a result

`run_station` returns a `StationResult`. It is a plain record that holds refs, so it can be saved and compared.

| Field | Meaning |
|---|---|
| `outcome` | One of the five outcomes below. |
| `ran` | Whether the station's own code completed and returned its output type. |
| `passed` | `True`, `False`, or `None` when the output was not judged. |
| `input` | The ref of the input artifact. |
| `output` | The ref of the output artifact, if there is one. |
| `evidence` | The refs of anything else the station recorded. |
| `decision` | The gate's decision, when the gate was evaluated. |
| `not_evaluated` | Why the gate could not be evaluated, with the evidence that was missing. |
| `reason` | Why the attempt did not run, in plain words. |
| `error_type`, `traceback` | The exception a station raised, kept as text so the record can be saved. |
| `station`, `station_version`, `run_id`, `attempt` | Which attempt this was. |

## The five outcomes

| Outcome | `ran` | `passed` | When |
|---|---|---|---|
| `passed` | `True` | `True` | The gate judged the output and passed it. |
| `failed` | `True` | `False` | The gate judged the output and failed it. |
| `needs_approval` | `True` | `None` | The gate is satisfied and a person must decide. |
| `not_judged` | `True` | `None` | The station has no gate. Nothing judged the output. |
| `did_not_run` | `False` | `None` | The station raised, or returned something that is not its output type. |
| `did_not_run` | `True` | `None` | The station's code ran, but its gate could not be evaluated. |

`ran` is about the station's own code. `outcome` is about the attempt as a whole. That is why `did_not_run` appears twice: an attempt whose gate could not be evaluated did not run to a judgement, even though its code ran.

Only a gate can fail a station. A station that raises has not failed: it did not run. The exception is not lost. Its type and traceback are on the result, and it is logged on the `pydantic_ai_factory` logger. This matters once stations are joined into a [line](lines.md), because a failure can send work back for rework and "did not run" never does.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import MemoryStore, StationContext, run_station, station


class Text(BaseModel):
    body: str


class Count(BaseModel):
    words: int


@station
async def unreachable(ctx: StationContext, text: Text) -> Count:
    raise ConnectionError("the tool is unreachable")


async def main() -> None:
    result = await run_station(unreachable, Text(body="hello"), store=MemoryStore())

    print(result.outcome, result.ran, result.passed)
    #> did_not_run False None
    print(result.reason)
    #> the station raised ConnectionError: the tool is unreachable


asyncio.run(main())
```

A station with no gate is allowed, and its result is honest about it: the outcome is `not_judged`, never `passed`.

## Recording evidence

A station often produces more than its output: a review report, a test log, a list of sources. Record each with `ctx.record`. It is stored at once, with the station's input as its lineage, and the gate can then read it.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    MemoryStore,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    run_station,
    station,
)


class Text(BaseModel):
    body: str


class Summary(BaseModel):
    text: str


class SpellCheck(BaseModel):
    misspelt: list[str]


@gate(version="1", requires=[SpellCheck])
def no_spelling_mistakes(output: Artifact[Summary], evidence: Evidence) -> Verdict:
    misspelt = evidence.one(SpellCheck).payload.misspelt
    return failed(f"misspelt: {', '.join(misspelt)}") if misspelt else passed("spelling is clean")


@station(gate=no_spelling_mistakes)
async def summarise(ctx: StationContext, text: Text) -> Summary:
    summary = Summary(text=text.body.split(".")[0])
    await ctx.record(SpellCheck(misspelt=[w for w in summary.text.split() if w == "teh"]))
    return summary


async def main() -> None:
    result = await run_station(
        summarise, Text(body="Split teh bill. Then pay."), store=MemoryStore()
    )

    print(result.outcome)
    #> failed
    assert result.decision is not None
    print(result.decision.reasons)
    #> ('misspelt: teh',)
    print(len(result.evidence))
    #> 1


asyncio.run(main())
```

If the station forgets to record a `SpellCheck`, the gate is not evaluated and the outcome is `did_not_run`. Missing evidence is never a pass. The [gates](gates.md) page explains why.

## The context

The first argument to a station is a `StationContext`.

| Attribute | What it gives you |
|---|---|
| `ctx.deps` | Whatever you passed as `deps=` to `run_station`: clients, settings, an agent. |
| `ctx.input` | The input as an artifact, with its ref. |
| `ctx.record(payload)` | Records evidence beside the output. |
| `ctx.attempt` | Which attempt this is, starting at 1. |
| `ctx.producer` | The station, version, run and attempt, as stamped on what this run makes. |
| `ctx.feedback` | The gate decision that failed the previous attempt, when this is rework. |
| `ctx.store` | The artifact store. |
| `ctx.run_id`, `ctx.mission_id`, `ctx.methodologies` | Where this run sits. |

The context cannot be changed, so a station cannot alter the record of its own run. Evidence must be recorded before the station returns; recording later raises `StationClosed`.

Give `deps` a type by writing `StationContext[MyDeps]`. This is how a station is tested: pass a fake in `deps`. The type guides your editor inside the station. It is not checked where `run_station` is called, so passing the wrong `deps` shows up when the station uses it.

```python
import asyncio
from dataclasses import dataclass

from pydantic import BaseModel

from pydantic_ai_factory import MemoryStore, StationContext, run_station, station


class City(BaseModel):
    name: str


class Forecast(BaseModel):
    summary: str


@dataclass
class Deps:
    weather: dict[str, str]


@station
async def forecast(ctx: StationContext[Deps], city: City) -> Forecast:
    return Forecast(summary=ctx.deps.weather[city.name])


async def main() -> None:
    result = await run_station(
        forecast, City(name="Leeds"), store=MemoryStore(), deps=Deps(weather={"Leeds": "rain"})
    )
    print(result.outcome)
    #> not_judged


asyncio.run(main())
```

## Declaring a station

The decorator takes three optional arguments.

| Argument | Default | Meaning |
|---|---|---|
| `name` | the function's name | The station's name in every record. |
| `version` | `"0"` | The version of the station's code. Change it when behaviour changes. |
| `gate` | none | The gate that judges the output. |

A station is refused when it is declared, not when it runs, if it is not async, does not take exactly two positional arguments, has an empty name or version, or its input or output type is not a Pydantic model. The error is a `StationDefinitionError` that says which rule was broken.

Passing the wrong input, or a bad `attempt`, `run_id` or `mission_id`, raises `StationInputInvalid` before anything runs.

An error from the store while saving the input or the output is raised as it is: a store that cannot write is not something a result can record.

## Where agents fit

A station's function is ordinary async Python, so it can call a Pydantic AI agent like any other code. Nothing on this page needs one. The Design station, later in the series, shows a station built around agents and a kit of capabilities.
