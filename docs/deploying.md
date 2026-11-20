---
title: Deploying a factory
description: How to run a factory as a service, with a web process that holds no model key, a worker that does the work, one shared store and a queue between them.
---

# Deploying a factory

Deploying a factory means running it for other people, not from your own Python session. People start missions and answer waiting runs from a web page, and the work happens somewhere else. This page explains how to split that work between two processes, which store to choose, where the queue lives, who the person answering is, and how to swap in a factory of your own.

It follows a working example, [the software factory app](https://github.com/hnrly/pydantic-ai-factory/tree/main/examples/software-factory-app). That folder runs the software factory as a small service, with a FastAPI web process, a worker and a React interface. Copy it to start your own. [Operating a factory](operating.md) covers the day-to-day work once it runs: finding waiting runs, answering them and spotting stalled ones.

## Two processes

The example runs as two processes that share one store:

- **The web process** answers requests from the browser. It lists runs, shows one, checks that an answer fits the run, and adds a job to a queue. It never runs the factory.
- **The worker** takes each job from the queue and does it. It starts a run with `factory.run`, or carries a waiting run on with `factory.resume`.

A run makes several model requests and can take minutes. A web request should not wait that long. Work started inside the web process, as a background task, is also lost when the web process restarts, which happens on every deploy. A separate worker keeps both problems away from the web page.

Answers go through the queue too, not only new missions. Asking for changes sends a design back to the designer, so an answer can start model work as well.

## Which process holds the model key

Only the worker holds the model key. It is the only process that runs a factory, so it is the only one that calls a model. The web process never needs the key, and never should have it.

The web process still needs the factory's shape, to tell whether a run was made by the factory it serves. A factory's digest covers its stations, their versions and its rework rules, not the model, so the web process can build the same factory on a model that needs no key:

```python
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from pydantic_ai_factory import Factory, station


def build_factory(model: Model | str) -> Factory:
    designer = Agent(model, name="designer", instructions="Write a short design.")
    return Factory("software", [station(designer, approval=True)])


in_the_worker = build_factory("anthropic:claude-opus-5-5")
in_the_web_process = build_factory(TestModel())
print(in_the_worker.digest == in_the_web_process.digest)
#> True
```

The example's web process uses the software factory's scripted model, `offline_model`, in the same way.

## Choosing a store

Both processes must open the same store. A run keeps nothing in the process that started it, so whatever one process saves, the other can load.

- **`SQLiteStore`** suits one machine: a web process and a worker that share one file. The example uses it. Keep the file on a local disk, not a network share.
- **Your own store** suits more than one machine, such as several workers behind a load balancer. Keep runs in a database that every machine can reach. [Your own store](stores.md#your-own-store) lists the rules it must keep, and the tests that check them.

`MemoryStore` does not suit a deployment. Each process has its own, and a restart loses it. [Stores](stores.md) compares them all.

## The queue

The example's queue is a `jobs` table in the same SQLite file as the store. The web process adds a row. The worker claims the oldest queued row in one statement, so two workers never claim the same job. A job is `start`, to run a mission, or `answer`, to carry a waiting run on with what a person chose. It ends `done`, or `failed` with a reason a person can read.

The web process chooses a new run's id when it queues the start job. So the browser can open the run's page at once, before the worker has saved anything. The worker passes that id to `factory.run`:

```python
import uuid

from pydantic_ai import Agent

from pydantic_ai_factory import Factory, RunNotFound, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore("factory.db")

# The web process chooses the id and queues a start job with it.
run_id = uuid.uuid4().hex
try:
    store.get_run_sync(run_id)
except RunNotFound:
    print("queued")
    #> queued

# Later, the worker claims the job and starts the run under that id.
software.run_sync("A to-do list web page.", store=store, run_id=run_id)
print(store.get_run_sync(run_id).outcome)
#> waiting
```

The queue belongs to the example, not the library. A queue is part of how you deploy. You may already have one, such as Celery, a cloud task queue or your database's own, and the library should not choose for you. It gives each process what it needs to share the work instead: a store any process can open, `list_runs` to find runs, and `run_id=` to name a run before it starts. The [decisions](decisions.md#a-store-lists-its-runs-and-the-queue-belongs-to-the-deployment) page says why.

## Who answered

Every answer records who gave it, in `by`. In the example, `by` is a name the person types into the interface. It is a name, not a sign-in: anyone can type any name, and the interface says so.

In a real deployment, your sign-in sets `by`. The web process takes the name from the signed-in session, never from the request body, and puts it in the answer job. The worker passes it to the run's answer:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = SQLiteStore("factory.db")
run_id = software.run_sync("A to-do list web page.", store=store).run_id

signed_in_as = "ada"  # From your sign-in, such as the session's user, never from the form.
run = store.get_run_sync(run_id)
done = software.resume_sync(run.approve(by=signed_in_as), store=store)
print(done.answers[0].by)
#> ada
```

The run keeps that name for good, so the record says who approved the work.

## Your own factory

In the example, one file knows which factory it runs: `app/factory.py`. The worker, the API and the interface work with any factory. Change the file's two functions and they run yours:

- `build_factory(model)` returns the factory, on `model`. The worker calls it with the real model. The web process calls it with a scripted one, only to read the digest.
- `render_output(run, store)` returns the Markdown a run's page shows for the work the run waits on or produced, or `None` when there is nothing to show.

Here is a version for a factory whose designer writes plain text, in place of the software factory's `Design`:

```python
import asyncio

from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, Run, SQLiteStore, station
from pydantic_ai_factory.factory import Store


def build_factory(model: Model | str) -> Factory:
    designer = Agent(model, name="designer", instructions="Write a short design.")
    return Factory("software", [station(designer, approval=True)])


async def render_output(run: Run, store: Store) -> str | None:
    work = run.output if run.output is not None else (run.waiting.output if run.waiting else None)
    return None if work is None else f"# The design\n\n{work}\n"


async def main() -> None:
    store = SQLiteStore("factory.db")
    run = await build_factory("anthropic:claude-opus-5-5").run("A to-do list web page.", store=store)
    page = await render_output(run, store)
    print(page.splitlines()[0] if page else "nothing to show")
    #> # The design


asyncio.run(main())
```

`build_factory` must not need a key just to build the factory, because the web process calls it too.

## Before you change the factory's shape

The library will not resume a run made by a factory of another shape. A run records its factory's digest, and `factory.resume` refuses a run whose digest differs. So adding a station, changing one's version or changing its agents strands every run that waits on the old shape.

Before you deploy a new shape, answer or reject the runs that are waiting. To find the ones a new shape could not resume, compare digests:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, SQLiteStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
store = SQLiteStore("factory.db")

running_now = Factory("software", [station(designer, approval=True)])
running_now.run_sync("A to-do list web page.", store=store)

next_release = Factory("software", [station(designer, approval=True), station(builder)])
stranded = [run for run in store.list_runs_sync(outcome="waiting") if run.factory_digest != next_release.digest]
print(len(stranded))
#> 1
```

Answer those with the factory that is running now, then deploy. The example's interface marks any run left behind as made by an earlier version, and offers no actions on it. [Operating a factory](operating.md#before-changing-a-factorys-shape) shows the refusal.

## Stalled runs today

A run is saved between stations, not during one. If the worker dies part-way through a station, the run stays saved as `"running"` and stops changing. The example shows it as stalled once it has gone 10 minutes without a change. Set `FACTORY_STALLED_MINUTES` above your slowest real station. A job that a worker claimed and never finished is shown the same way.

A stalled run cannot be carried on yet. `factory.resume` carries on only a run that waits for a person. Carrying on a run that stopped part-way needs [durable runs](roadmap.md#next), which are on the roadmap. Until then, start the mission again as a new run: the example's interface offers "Start again" for this. [Stalled runs](operating.md#stalled-runs) explains how to spot one from `updated_at`.
