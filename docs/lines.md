---
title: Lines and rework
description: How to join stations into a line, send failed work back a bounded number of times, and read the record of a run.
---

# Lines and rework

A line is an ordered route through [stations](stations.md). Each station's output becomes the next station's input. When a station fails its [gate](gates.md), the line can send the work back a fixed number of times; this is rework. This page shows how to declare a line, run work along it and read the record of what happened.

A line is plain data, and one function runs it. It runs in-process: there is no server to start and no engine to install.

## Declare and run a line

This line has three stations and no model. `write` produces a draft. `check` fails drafts with fewer than three words. `publish` gives the checked draft an address.

```python
import asyncio

from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    Line,
    MemoryStore,
    Rework,
    StationContext,
    Verdict,
    failed,
    gate,
    passed,
    run_line,
    station,
)


class Brief(BaseModel):
    topic: str


class Draft(BaseModel):
    text: str


class Checked(BaseModel):
    text: str
    words: int


class Published(BaseModel):
    address: str


@station
async def write(ctx: StationContext, brief: Brief) -> Draft:
    # On rework, the gate's reasons arrive as feedback. Here they prompt a longer draft.
    if ctx.feedback:
        return Draft(text=f"A short guide to {brief.topic}")
    return Draft(text=brief.topic)


@gate(version="1")
def long_enough(output: Artifact[Checked], evidence: Evidence) -> Verdict:
    if output.payload.words < 3:
        return failed(f"only {output.payload.words} words; at least 3 are needed")
    return passed(f"{output.payload.words} words")


@station(gate=long_enough)
async def check(ctx: StationContext, draft: Draft) -> Checked:
    return Checked(text=draft.text, words=len(draft.text.split()))


@station
async def publish(ctx: StationContext, checked: Checked) -> Published:
    return Published(address=f"https://example.org/{checked.words}-words")


guide = Line(
    "guide",
    [write, check, publish],
    rework=[Rework("check", "write", limit=2)],
)


async def main() -> None:
    store = MemoryStore()
    run = await run_line(guide, Brief(topic="tea"), store=store)

    print(run.outcome, run.finished)
    # > passed True
    for step in run.steps:
        print(step.station, step.attempt, step.outcome)
        # > write 1 not_judged
        # > check 1 failed
        # > write 2 not_judged
        # > check 2 passed
        # > publish 1 not_judged

    assert run.output is not None
    print((await store.get(run.output, Published)).payload.address)
    # > https://example.org/5-words


asyncio.run(main())
```

The first draft was one word, so `check` failed it. The rework rule sent the work back to `write`, which received the gate's reasons in `ctx.feedback` and wrote a longer draft. The second check passed and the line carried on.

## Rework

A rework rule says what happens when one station fails its gate.

```python test="skip"
Rework("check", "write", limit=2, on_limit="halt")
```

This block shows the shape only; the full example above runs it.

| Argument | Meaning |
|---|---|
| `from_station` | The station whose failure triggers the rule. |
| `to_station` | Where the work goes back to. It may be the same station or an earlier one. Leave it out to redo the failing station itself. |
| `limit` | How many times work may be sent back. The default is 1. |
| `on_limit` | What to do when the limit is reached: `"halt"`, the default, or `"continue"`. |

When work is sent back, the line runs forward again from the target. The target station gets the failing gate's decision as `ctx.feedback`, so it can act on the reasons. Its `ctx.attempt` goes up by one.

Three rules are worth knowing.

1. **Rework is only for failures.** A station that did not run is never sent back. The line stops and reports it. An unreachable tool is not fixed by redoing the work.
2. **A failure with no rule stops the line.** Nothing is retried unless you say so.
3. **Rework is always bounded.** There is no unlimited setting.

### Carrying on at the limit

With `on_limit="continue"`, a line that has used up its rework moves on to the next station anyway. The failure is not forgotten. The station's result stays `failed`, and the run's outcome is `failed` even though it finished. Use this when a later station is worth running on imperfect work, such as one that reports what went wrong.

## The record of a run

`run_line` returns a `LineRun`. It is a plain record that holds refs, so it can be saved, compared and sent elsewhere.

| Field | Meaning |
|---|---|
| `outcome` | `passed`, `failed`, `did_not_run` or `needs_approval`. |
| `finished` | Whether the work reached the end of the line. |
| `stopped_at` | The station where the line stopped early, if it did. |
| `steps` | One station result per attempt, in the order they happened. |
| `input`, `output` | The refs of the first input and the final output. |
| `run_id`, `line`, `line_version` | Which run of which line this was. |
| `mission_id`, `methodologies` | Where the run sits. These are stamped on every artifact it makes. |

`outcome` and `finished` are separate answers, as `ran` and `passed` are for a station.

| Outcome | `finished` | When |
|---|---|---|
| `passed` | `True` | The work reached the end and no station failed. |
| `failed` | `False` | A station failed with no rework left, and the line halted. |
| `failed` | `True` | A station failed, its rule said to carry on, and the work reached the end. |
| `did_not_run` | `False` | A station raised, returned the wrong type, or its gate could not be evaluated. |
| `needs_approval` | `False` | A gate asked for a person. The line waits there. |

A station with no gate is `not_judged`, and the line moves past it. Such a line can end as `passed`: nothing failed. Give a station a gate when its output must be judged.

Two helpers read the steps for you.

| Call | Returns |
|---|---|
| `run.latest("check")` | The most recent result for that station, or `None` if it never ran. |
| `run.attempts("check")` | How many times it was attempted. |

## Lineage

Every station's output names its input, so a finished run is a chain you can walk backwards. The last sample on the [artifacts](artifacts.md#read-without-the-payload-class) page shows how to follow `inputs` without knowing each payload's class.

All the artifacts a run makes carry the same `run_id` in `produced_by`, with the station and the attempt.

## A badly formed line is refused

A line is checked when it is declared, not when it runs. If anything is wrong you get one `LineDefinitionError` whose `findings` list every problem at once.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Line, LineDefinitionError, Rework, StationContext, station


class Brief(BaseModel):
    topic: str


class Draft(BaseModel):
    text: str


class Published(BaseModel):
    address: str


@station
async def write(ctx: StationContext, brief: Brief) -> Draft:
    return Draft(text=brief.topic)


@station
async def publish(ctx: StationContext, published: Published) -> Published:
    return published


try:
    Line("guide", [write, publish], rework=[Rework("review")])
except LineDefinitionError as error:
    for finding in error.findings:
        print(finding)
        # > 'write' hands on Draft but 'publish' takes Published
        # > rework from 'review': no such station
```

A line is refused when:

- it has no stations, or two stations share a name;
- a station's output type is not the next station's input type;
- a rework rule names a station that is not in the line, or a target that comes later;
- a rework rule has a limit below 1, or is declared twice for one station;
- a rework rule starts from a station with no gate, which can never fail.

## Passing dependencies

`run_line` takes one `deps` object and gives it to every station as `ctx.deps`. Use it for clients, settings and agents, and pass a fake in tests.

## What comes next

A line that stops at `needs_approval` cannot yet be resumed. That is the next page: a person's decision, bound to the exact artifact they approved.
