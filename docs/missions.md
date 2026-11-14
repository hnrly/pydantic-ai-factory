---
title: Missions and signals
description: A mission is what a factory commits to. Signals are how work can arrive on its own.
---

# Missions and signals

A mission is what a factory commits to: an objective, how to tell it was met, and any constraints. Most of the time a string is enough, and becomes a mission's objective.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, Mission, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
design = station(designer)
build = station(builder)

software = Factory("software", [design, build])
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
print(run.outcome)
#> passed
print(store.get_sync(run.mission_ref, Mission).payload.objective)
#> A to-do list web page where I can add tasks and tick them off.
```

A string becomes a mission when the first station takes a `Mission` or a string. A station that takes a string, such as `station(designer)`, is given the mission as its prompt: the objective, then the success criteria and constraints, if there are any. `mission.as_prompt()` returns that text. A factory whose first station takes anything else runs its input with no mission.

Build one when you want to say more:

```python
from pydantic_ai_factory import Mission

mission = Mission(
    "A to-do list web page where I can add tasks and tick them off.",
    success_criteria=("Ticked tasks stay ticked after a reload.",),
    constraints=("One HTML file, no server.",),
)
print(mission.objective)
#> A to-do list web page where I can add tasks and tick them off.
```

Run it the same way, with `software.run_sync(mission, store=store)`. A mission is a frozen record with an `id`. The run keeps it as an artifact, named by `run.mission_ref`, and every artifact the run makes carries its id, so each output traces back to what it was for. The library does not judge the success criteria itself: write [gates](gates.md) that check them, and let a person approve the rest.

## Signals

Sometimes work arrives instead of being handed over: a bug report, a failing check, a message in a chat. A signal is that piece of data, and its type says what kind it is. A factory's intake reads signals and returns a mission, or declines with a reason.

```python
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from pydantic_ai_factory import Declined, Factory, MemoryStore, Mission, Signal, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
design = station(designer)
build = station(builder)


class BugReport(BaseModel):
    title: str


async def triage(signals: Sequence[Signal[Any]]) -> Mission | Declined:
    bugs = [s.payload for s in signals if isinstance(s.payload, BugReport)]
    if not bugs:
        return Declined("no bug report among the signals")
    return Mission(f"Fix the to-do list page: {bugs[0].title}")


software = Factory("software", [design, build], accept=triage)
store = MemoryStore()

report = Signal(BugReport(title="Ticked tasks come back after a reload"), source="github")
mission = software.intake_sync([report], store=store)
print(mission.objective)
#> Fix the to-do list page: Ticked tasks come back after a reload
print(len(mission.signals))
#> 1

run = software.run_sync(mission, store=store)
print(run.outcome)
#> passed
```

A signal's payload may be any value Pydantic can serialise. `source` says where it came from, and may be left out. `accept` may be an async function or a plain one.

Each signal is stored before intake decides, and the mission records which signals it came from in `mission.signals`. A `Declined` names them too. Intake does not start a run: you choose when to run the mission, as above. `intake` is the async form of `intake_sync`.

When a run of a mission made by intake ends, the factory emits an outcome signal, so a factory's results can become its next work. Its payload is an `Outcome`: the factory, the run, the mission, how the run ended and the ref of its output. The signal is stored, and `run.outcome_signal` names it. Read it back with `store.get_sync(run.outcome_signal, Signal[Outcome])`, and pass its payload to `intake` like any other signal.

A mission built by hand records no signals, and its runs emit nothing.
