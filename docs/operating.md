---
title: Operating a factory
description: How to find the runs waiting for a person, answer them from any process, hear when a run starts waiting, and spot a run that stalled.
---

# Operating a factory

Operating a factory means looking after its runs once they have started. Most of a run needs no one. Some of it waits for a person, and now and then a run stops part-way and never finishes. This page shows how to find the runs that need someone, answer them, hear about new ones, and spot the ones that stalled.

Everything here works through the [store](stores.md). A run keeps nothing in the process that started it, so any process that shares the store can operate it: a web app, a worker, a script run by hand. The samples use `SQLiteStore`, which lets several processes on one machine share the work.

## Finding waiting runs

`list_runs` finds runs without knowing their ids. Ask for the waiting ones, oldest first, and you have an inbox: the run at the top has waited longest.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, Mission, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore("factory.db")
software.run_sync("A to-do list web page.", store=store)
software.run_sync("A shopping list web page.", store=store)

for run in store.list_runs_sync(outcome="waiting", order="oldest"):
    mission = store.get_sync(run.mission_ref, Mission).payload
    print(run.waiting.kind, run.waiting.station, mission.objective)
    #> approval designer A to-do list web page.
    #> approval designer A shopping list web page.
```

A waiting run's `updated_at` is when it started waiting, so it tells you how long it has waited. Pass `factory="software"` to list one factory's runs. [Listing runs](stores.md#listing-runs) has the rest, such as the limit.

## Answering a waiting run

Load the run by its id, answer it and pass the answer to `factory.resume`. The answer names its run, so `resume` loads the run from the store itself. It need not be the process that started the run. It only needs the same store and a factory of the same shape.

A run waits for one of three things. `run.waiting.kind` says which:

- `"approval"`: a station with `approval=True` passed its checks. Answer with `run.approve`, `run.request_changes` or `run.reject`.
- `"escalation"`: something only a person should settle, such as a check that still fails after the allowed retries. The same three answers apply. Approving accepts the work as it stands.
- `"tool-calls"`: an agent asked a person a question, or asked before an action. `run.question` says what it asked; answer with `run.reply("...", by=...)`. For an action, answer with `run.allow` or `run.deny`.

Here the designer can ask questions, through [`AskPerson`](approvals.md#tool-calls), and its design needs approval. Its run waits twice, and each answer comes from a fresh store object, as it would in another process:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import AskPerson, Factory, SQLiteStore, station

designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    instructions="Write a short design.",
    capabilities=[AskPerson()],
)
software = Factory("software", [station(designer, approval=True)])
run_id = software.run_sync("A to-do list web page.", store=SQLiteStore("factory.db")).run_id

# Later, in another process: a web request, a worker or a command.
store = SQLiteStore("factory.db")
run = store.get_run_sync(run_id)
print(run.waiting.kind)
#> tool-calls
print(run.question)
#> Should ticked tasks stay after a reload?
answer = run.reply("Yes, keep them.", by="ada")
run = software.resume_sync(answer, store=store)

store = SQLiteStore("factory.db")
run = store.get_run_sync(run_id)
print(run.waiting.kind)
#> approval
answer = run.approve(by="ada")
done = software.resume_sync(answer, store=store)
print(done.outcome)
#> passed
```

[Approvals](approvals.md) says what each answer does to the run. Every answer, tool replies included, is recorded in `run.answers`.

## Hearing when a run starts waiting

Pass `on_step=` to `run` or `resume`. It is awaited with the run each time the run is saved. When the run's outcome is `"waiting"`, tell someone:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, Run, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])


async def tell_when_waiting(run: Run) -> None:
    if run.outcome == "waiting":
        # Send this to your chat tool or email instead.
        print(f"A run waits for {run.waiting.kind} at {run.waiting.station}.")
        #> A run waits for approval at designer.


software.run_sync("A to-do list web page.", store=SQLiteStore("factory.db"), on_step=tell_when_waiting)
```

In a real message, include `run.run_id`, so the person can open the run.

Keep `on_step` short, and catch your own errors inside it. The next station waits for it to return. If it raises, the run stays saved as `"running"` and [stalls](#stalled-runs). [Watching a run](factories.md#watching-a-run) has the details.

## When two people answer at once

Two people can open the same waiting run and answer it together. Each answer carries the revision its person saw. The first answer is saved. The second gets `RunConflict` before any of its work runs, so a design is never both approved and rejected. To find out first, without running anything, call `factory.check(run, answer)`: its problem has `stale=True`. Load the run again and show the person what happened:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, RunConflict, SQLiteStore, StoreBusy, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore("factory.db")
run_id = software.run_sync("A to-do list web page.", store=store).run_id

adas_page = store.get_run_sync(run_id)
bobs_page = store.get_run_sync(run_id)
software.resume_sync(adas_page.approve(by="ada"), store=store)

try:
    software.resume_sync(bobs_page.reject(by="bob", note="Not now."), store=store)
except StoreBusy:
    print("The store was busy. Try again.")
except RunConflict:
    latest = store.get_run_sync(run_id)
    print(f"Someone answered first. The run has {latest.outcome}.")
    #> Someone answered first. The run has passed.
```

`StoreBusy` is a kind of `RunConflict`, so catch it first. It does not mean someone answered first. It means another process held the store for longer than `busy_timeout`, and nothing was saved. Trying again is safe. See [SQLiteStore](stores.md#sqlitestore).

## Stalled runs

A run is stalled when its outcome is `"running"` and its `updated_at` is older than a threshold you choose, such as ten minutes.

A run is saved between stations, not during one. So a live station that takes longer than the threshold looks the same as a stalled run until it finishes. Choose a threshold above your slowest real station, and say "no change since" rather than "dead".

A run stalls when something stops it mid-run without a save:

- the process running it died, or was stopped, part-way through a station;
- an `on_step` raised, which ends the call and leaves the run saved as `"running"`.

To list them, ask the store for running runs last saved before the threshold, oldest first:

```python
from datetime import UTC, datetime, timedelta

from pydantic_ai import Agent

from pydantic_ai_factory import Factory, Run, SQLiteStore, station

STALLED_AFTER = timedelta(minutes=10)


def stalled_runs(store: SQLiteStore, now: datetime | None = None) -> list[Run]:
    cutoff = (now or datetime.now(UTC)) - STALLED_AFTER
    return store.list_runs_sync(outcome="running", updated_before=cutoff, order="oldest")


designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
software = Factory("software", [station(designer), station(builder)])
store = SQLiteStore("factory.db")


async def broken_notifier(run: Run) -> None:
    if run.steps:
        raise ConnectionError("the chat tool did not answer")


try:
    software.run_sync("A to-do list web page.", store=store, on_step=broken_notifier)
except ConnectionError:
    pass

print(len(stalled_runs(store)))
#> 0
print(len(stalled_runs(store, now=datetime.now(UTC) + timedelta(minutes=11))))
#> 1
```

The run is not stalled straight away: it looks like a live station for the first ten minutes.

To end a stalled run whose worker has died, call [`factory.abandon`](factories.md#ending-a-run-that-is-stuck). It saves the run as `"did_not_run"` with your reason, and a worker that was only slow gets `RunConflict` on its next save.

A stalled run cannot be resumed yet. `factory.resume` carries on only a run that is waiting for a person. Carrying on a run that stopped part-way needs [durable runs](roadmap.md#next), which are on the roadmap. Until then, start the mission again as a new run.

## Before changing a factory's shape

A run records its factory's digest, which covers the stations, their versions and the rework rules. `factory.resume` refuses a run made by a factory with another digest, with `ApprovalRefused`. So before you add a station, change one's version or change its agents, answer or reject the runs that are waiting.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import ApprovalRefused, Factory, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
store = SQLiteStore("factory.db")

before = Factory("software", [station(designer, approval=True)])
run = before.run_sync("A to-do list web page.", store=store)

after = Factory("software", [station(designer, approval=True), station(builder)])
try:
    after.resume_sync(run.approve(by="ada"), store=store)
except ApprovalRefused as error:
    print("refused:", "another shape" in str(error))
    #> refused: True
```

The old factory can still answer it, so keep it until its waiting runs are done.
