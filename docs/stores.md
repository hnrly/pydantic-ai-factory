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
| `SQLiteStore(path)` | In one SQLite file | A service on one machine, such as a web app and its worker. |

## Artifacts

An artifact is the record of what a station handed on: a design, a review report, an agent's conversation. It is never changed after it is written, and it is named by a digest of its content. That is what lets a person approve one exact design: change a word and it is a different artifact.

Every artifact names what it was made from and which station, run and methodologies made it. So any output can be traced back to the mission that started it.

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
done = software.resume_sync(run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

A run read from a store holds its payloads as plain JSON. `factory.resume` reads what it needs from the store with its type, and so can you: `store.get_sync(run.output_ref, str)`.

Each save is a compare-and-swap. An answer is saved before any of the work it starts, so if two people answer the same run at once, one wins and the other gets `RunConflict` before anything runs. A design cannot be both approved and rejected, and approved work is never built twice.

## Listing runs

`list_runs` finds runs without knowing their ids. It sorts them by `updated_at`, the time each was last saved, newest first. Pass `order="oldest"` for oldest first. Filters list only some runs, and each one given must match:

| Filter | Lists only runs |
|---|---|
| `outcome` | With this outcome, or with any of several, such as `["running", "waiting"]`. |
| `factory` | Of the factory with this name. |
| `updated_after` | Last saved strictly after this moment. |
| `updated_before` | Last saved strictly before this moment. With `before_run_id`, a cursor that pages back exactly: see [paging back](#paging-back). |

So the runs that have waited longest for a person are one call away:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
first = software.run_sync("A to-do list web page.", store=store)
second = software.run_sync("A shopping list web page.", store=store)
software.resume_sync(second.approve(by="ada"), store=store)

waiting = store.list_runs_sync(outcome="waiting", order="oldest")
print(len(waiting), waiting[0].run_id == first.run_id, waiting[0].waiting.station)
#> 1 True designer
```

A waiting run's `updated_at` is when it started waiting. Runs saved at the same moment are sorted by `run_id` the same way: newest first puts the last `run_id` first.

### Polling for changes

`updated_after` lets a page that polls ask only for what changed since its last look. Do not pass the time on your own clock when you looked. A save stamps `updated_at` just before it is written, so a save stamped a moment before your look may only become visible after it, and asking from your clock would miss it for ever.

Ask from the newest `updated_at` you have seen instead, less a small overlap, and drop the saves you have already seen. Choose an overlap longer than any save takes; ten seconds is plenty for the stores the library ships. Ask oldest first, so if more runs changed than `limit` allows, the next poll carries on where this one stopped. Both moments must carry a time zone, or `list_runs` raises `ValueError`.

```python
from datetime import datetime, timedelta

from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, Run, station

OVERLAP = timedelta(seconds=10)


class Poller:
    """Asks a store for the runs saved since its last look."""

    def __init__(self, store: MemoryStore) -> None:
        self.store = store
        self.newest: datetime | None = None
        self.seen: set[tuple[str, datetime]] = set()

    def changed(self) -> list[Run]:
        since = None if self.newest is None else self.newest - OVERLAP
        runs = self.store.list_runs_sync(updated_after=since, order="oldest", limit=1000)
        fresh = [run for run in runs if (run.run_id, run.updated_at) not in self.seen]
        for run in fresh:
            self.seen.add((run.run_id, run.updated_at))
            self.newest = run.updated_at if self.newest is None else max(self.newest, run.updated_at)
        if self.newest is not None:
            # Saves older than the overlap will not be asked for again, so forget them.
            self.seen = {save for save in self.seen if save[1] > self.newest - OVERLAP}
        return fresh


designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
poller = Poller(store)
first = software.run_sync("A to-do list web page.", store=store)
print([run.run_id == first.run_id for run in poller.changed()])
#> [True]
print(poller.changed())
#> []
second = software.run_sync("A shopping list web page.", store=store)
print([run.run_id == second.run_id for run in poller.changed()])
#> [True]
```

### Paging back

`updated_before` pages back through older runs. Pass the `updated_at` and the `run_id` of the last run on one page, as `updated_before` and `before_run_id`, to get the next. Together they are a cursor: the next page starts just after that run, so no run saved at the same moment is lost or shown twice. `before_run_id` without `updated_before` raises `ValueError`. Without `before_run_id`, `updated_before` keeps only runs saved strictly before that moment.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
for mission in ("A to-do list.", "A shopping list.", "A reading list."):
    software.run_sync(mission, store=store)

sizes = []
page = store.list_runs_sync(limit=2)
while page:
    sizes.append(len(page))
    last = page[-1]
    page = store.list_runs_sync(limit=2, updated_before=last.updated_at, before_run_id=last.run_id)
print(sizes)
#> [2, 1]
```

`limit` caps how many runs come back. It is 50 unless you say otherwise, and it must be between 1 and 1000, or `list_runs` raises `ValueError`. To see more, page back as above.

`LocalStore` reads every run file to answer. That is quick for the hundreds of runs one machine makes. `SQLiteStore` answers from an index, and puts only the filters you give into its query.

## SQLiteStore

`SQLiteStore(path)` keeps artifacts and runs in one SQLite file. It creates the file and its tables the first time it is used. It needs nothing beyond Python's standard library.

Choose it when more than one process on one machine shares the work, such as a web app that answers runs and a worker that does them. The file uses SQLite's write-ahead log, so one process can read while another writes. Choose `LocalStore` when you want to read each run as a plain file, or keep the store on a shared disk.

Two processes can save the same run at once. One wins, and the other gets `RunConflict`, as with every store. A save waits up to `busy_timeout` seconds, 5 by default, for another process to finish writing. If it waits longer, it raises `StoreBusy`, which is a kind of `RunConflict`. So `RunConflict` means someone answered first, unless it is a `StoreBusy`, which means try again.

The file keeps its schema version, now 2, in SQLite's own `user_version` slot. A file written with another schema version, by another version of the library, is refused with `StoreSchemaMismatch`. Its message names both versions. A file that has the store's tables, `artifacts` or `runs`, but no version is refused the same way. Other tables may share the file: the example app keeps its jobs table there.

`SQLiteStore` is for one machine. Do not keep its file on a network share: SQLite's locks are not reliable there.

## In a web app

A web app keeps nothing between requests. Each request loads the run, answers it and returns. Give it a store that outlives the process, such as `SQLiteStore`. A `MemoryStore` is lost when the app restarts, and each worker process would have its own. With any web framework, the handler is a few lines:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore("factory.db")


async def approve(run_id: str, user_name: str) -> dict[str, str]:
    """Route POST /runs/{run_id}/approve here, after your sign-in has named the user."""
    run = await store.get_run(run_id)
    done = await software.resume(run.approve(by=user_name), store=store)
    return {"outcome": done.outcome}
```

A run makes several model requests, so start long runs in a background task and let the client poll for the run.

## Your own store

A store is any object with the async methods of two protocols: `ArtifactStore` (`put`, `get`, `exists`) and `RunStore` (`get_run`, `put_run`, `list_runs`). It may keep artifacts in one place, such as object storage, and runs in another, such as a database. `pydantic_ai_factory.stores` has helpers that do the integrity checks for you: `encode_artifact` and `decode_artifact` check every artifact against its digest, and `encode_run` and `decode_run` read and write runs.

A store written for version 0.1 must add `list_runs`, with all of its filters and its paging cursor. Every store keeps these rules:

- An artifact comes back from `get` exactly as it was put.
- `put` refuses an artifact that does not match its digest, and `get` refuses one asked for under the wrong ref. Both raise `ArtifactCorrupt`.
- A run comes back from `get_run` exactly as it was saved.
- A stored run that this version cannot read, such as one saved by version 0.1, one that holds `approvals` instead of `answers`, or one without a `station`, raises `StoreSchemaMismatch`. `decode_run` does this for you.
- `put_run(run, replaces=previous)` saves only if the stored run is still `previous`. Otherwise it raises `RunConflict` and saves nothing.
- `put_run(run, replaces=None)` saves only if no run has that id. Otherwise it raises `RunConflict`.
- `list_runs` returns the latest save of each run, filtered by `outcome` (one or several), `factory`, `updated_after` and `updated_before` when given. The two moments are exclusive.
- Given `before_run_id` with `updated_before`, it returns only runs whose `(updated_at, run_id)` comes before `(updated_before, before_run_id)`.
- It raises `ValueError` for an empty list of outcomes, a moment without a time zone, or `before_run_id` without `updated_before`.
- It sorts by `updated_at`, newest first or oldest first, and runs saved at the same moment by `run_id` in the same direction.
- It returns at most `limit` runs, and raises `ValueError` when `limit` is not between 1 and 1000.

The library's own tests check these rules against every store it ships, in `tests/test_store_contract.py`. Copy them to check your own.
