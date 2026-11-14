---
title: Stores
description: A store keeps everything a factory makes, and every run, so work can be resumed anywhere.
---

# Stores

A store keeps two things: the artifacts a factory makes, and the runs that made them. Because everything lives in the store and nothing lives in the factory, a run started in one process can be answered in another, days later.

| Store | Keeps things | Use it for |
|---|---|---|
| `MemoryStore()` | In memory | Tests and scripts. |
| `LocalStore(folder)` | As files in a folder | Real work on one machine, or shared disk. |

## Artifacts

An artifact is the record of what a station handed on: a design, a review report, an agent's conversation. It is never changed after it is written, and it is named by a digest of its content. That is what lets a person approve one exact design: change a word and it is a different artifact.

Every artifact names what it was made from and which station, run and methodologies made it. So any output can be traced back to what started it.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer)])
store = MemoryStore()
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)

step = run.steps[0]
design = store.get_sync(step.output_ref, str)
print(design.produced_by.station, design.inputs == (run.input_ref,))
#> designer True
```

## Runs

`run` and `resume` save the run in the store after every step. Load it again by id:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
run_id = software.run_sync("A to-do list web page.", store=store).run_id

run = store.get_run_sync(run_id)
done = software.resume_sync(run, run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

A run read from a store holds its payloads as plain JSON. `factory.resume` reads what it needs from the store with its type, and so can you: `store.get_sync(run.output_ref, str)`.

Each save is a compare-and-swap. An answer is saved before any of the work it starts, so if two people answer the same run at once, one wins and the other gets `RunConflict` before anything runs. A design cannot be both approved and rejected, and approved work is never built twice.

## In a web app

A web app keeps nothing between requests. Each request loads the run, answers it and returns. With any web framework, the handler is a few lines:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()


async def approve(run_id: str, user_name: str) -> dict[str, str]:
    """Route POST /runs/{run_id}/approve here, after your sign-in has named the user."""
    run = await store.get_run(run_id)
    done = await software.resume(run, run.approve(by=user_name), store=store)
    return {"outcome": done.outcome}
```

A run makes several model requests, so start long runs in a background task and let the client poll for the run.

## Your own store

A store is any object with the async methods of two protocols: `ArtifactStore` (`put`, `get`, `exists`) and `RunStore` (`get_run`, `put_run`). It may keep artifacts in one place, such as object storage, and runs in another, such as a database. `pydantic_ai_factory.stores` has helpers that do the integrity checks for you: `encode_artifact` and `decode_artifact` check every artifact against its digest, and `encode_run` and `decode_run` read and write runs.
