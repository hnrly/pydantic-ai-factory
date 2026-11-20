---
title: Deploying a factory
description: How to run a factory as a service, with a web process that holds no model key, a worker that does the work, one shared store and a queue between them.
---

# Deploying a factory

Deploying a factory means running it for other people, not from your own Python session. People start missions and answer waiting runs from a web page, and the work happens somewhere else. This page explains how that work is split between two processes, which store to choose, how the queue between them works, who the person answering is, and what to watch when you change the factory.

The library does the serving for you. `board_app` and `work`, in `pydantic_ai_factory.serve`, give you the web process, its API, the board and the worker, from [one short file](board.md#one-file-serves-a-factory). [The software factory example](https://github.com/hnrly/pydantic-ai-factory/tree/main/examples/software-factory-app) is that file for the software factory. [Operating a factory](operating.md) covers the day-to-day work once it runs.

## Two processes

A served factory runs as two processes that share one store:

- **The web process**, `board_app(...)`, answers requests from the browser. It shows the board, checks that an answer fits its run, and adds a job to a queue. It never runs the factory.
- **The worker**, `work(...)`, takes each job from the queue and does it. It starts a run with `factory.run`, or carries a waiting run on with `factory.resume`.

A run makes several model requests and can take minutes. A web request should not wait that long. Work started inside the web process, as a background task, is also lost when the web process restarts, which happens on every deploy. A separate worker keeps both problems away from the web page.

Answers go through the queue too, not only new missions. Asking for changes sends a design back to the designer, so an answer can start model work as well.

## Which process holds the model key

Only the worker holds the model key. It is the only process that runs a factory, so it is the only one that calls a model. The web process never needs the key, and never should have it.

The web process still needs the factory's shape, to draw its columns and to tell whether a run was made by the factory it serves. A factory's digest covers its stations, their versions and its rework rules, not the model, so the web process can build the same factory on a model that needs no key:

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

`board_app` does exactly this: it calls your `build_factory` with `TestModel()`. So `build_factory` must not need a key just to build the factory.

## Choosing a store

Both processes must open the same store. A run keeps nothing in the process that started it, so whatever one process saves, the other can load.

- **`SQLiteStore`** suits one machine: a web process and a worker that share one file. The example uses it. Keep the file on a local disk, not a network share.
- **Your own store** suits more than one machine, such as several workers behind a load balancer. Keep runs in a database that every machine can reach. [Your own store](stores.md#your-own-store) lists the rules it must keep, and the tests that check them.

`MemoryStore` does not suit a deployment. Each process has its own, and a restart loses it. [Stores](stores.md) compares them all.

## The queue

The web process puts work on a queue, and the worker takes it off. `SQLiteDispatcher` is the queue that ships with the library. It keeps a `factory_jobs` table in the same SQLite file as `SQLiteStore`, and leaves the store's own tables alone.

- **A job** is `start`, to run a mission, or `answer`, to carry a waiting run on with a person's answer. It ends `done`, or `failed` with a reason a person can read.
- **The worker claims** the oldest queued job, so two workers never take the same one. It renews its claim while the job runs. A claim that lapses is shown as stalled, and the job is never handed to another worker.
- **The run's id** is chosen when a start job is queued. So the board shows the card at once, before the worker has saved anything. The worker passes that id to `factory.run`.

Here a mission is queued with no web process at all, and a worker does it:

```python
import asyncio

from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, Mission, RunNotFound, SQLiteStore, station
from pydantic_ai_factory.serve import SQLiteDispatcher, work


def build_factory(model: Model | str) -> Factory:
    designer = Agent(model, name="designer", instructions="Write a short design.")
    return Factory("software", [station(designer, approval=True)])


store = SQLiteStore("factory.db")
dispatcher = SQLiteDispatcher("factory.db")

job = asyncio.run(dispatcher.start(Mission("A to-do list web page."), by="ada"))
print(job.kind, job.state)
#> start queued
try:
    store.get_run_sync(job.run_id)
except RunNotFound:
    print("no run saved yet")
    #> no run saved yet

work(build_factory, store, dispatcher, model="anthropic:claude-opus-5-5", until_idle=True)
print(store.get_run_sync(job.run_id).outcome)
#> waiting
```

The web process talks to its queue only through the `Dispatcher` protocol: it queues starts and answers, and reads jobs back. You may already have a queue, such as Celery, a cloud task queue or a durable engine. `board_app` takes any `Dispatcher`, so a dispatcher of your own can put the same jobs on it, and the board works unchanged. But `work` drives only `SQLiteDispatcher`, so a dispatcher of your own needs a worker of your own, which claims its jobs and runs or resumes the factory. [Decisions](decisions.md#the-core-defines-a-dispatcher-and-serve-ships-one-simple-queue) says why the core defines the seam and ships one simple queue.

## Who answered

Every answer records who gave it, in `by`. On the board, `by` is the name the person gives on the board's first screen. It is a name, not a sign-in: anyone can type any name, and the board says so.

In a real deployment, your sign-in sets `by`. Pass `who=` to `board_app`, and the web process takes the name from the signed-in request, never from the request body. [Who is answering](board.md#who-is-answering) shows how. The answer job carries the name, and the run keeps it:

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

The board serves any factory whose first station takes a `Mission` or a `str`. Pass your own `build_factory` to `board_app` and `work`, in place of the example's `SoftwareFactory`.

The board shows each station's output in the run's panel, when a person opens its card. A station whose output is a string shows it as text, and any other output as Markdown, which the library writes from the output's shape. Pass `output_format=` to choose: here the designer writes Markdown itself, so the station asks for `MARKDOWN`:

```python
from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, OutputFormat, SQLiteStore, station


def build_factory(model: Model | str) -> Factory:
    designer = Agent(model, name="designer", instructions="Write a short design, in Markdown.")
    return Factory("software", [station(designer, approval=True, output_format=OutputFormat.MARKDOWN)])


factory = build_factory("anthropic:claude-opus-5-5")
run = factory.run_sync("A to-do list web page.", store=SQLiteStore("factory.db"))
print(factory.stations[0].output_format, factory.stations[0].show(run.waiting.output)[:9])
#> markdown One page,
```

`output_format` is not part of the factory's digest, so changing it never strands a waiting run. [How a station's output looks](stations.md#how-a-stations-output-looks) has the details.

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

Answer those with the factory that is running now, then deploy. The board shows any run left behind in Stopped, marked as an earlier version, with no answers to give. If such a run is still running or waiting, the board offers "Start again", which begins its mission again on the new shape. [Operating a factory](operating.md#before-changing-a-factorys-shape) shows the refusal.

## Stalled runs

A run is saved between stations, not during one. If the worker dies part-way through a station, the run stays saved as `"running"` and stops changing. The worker's claim on its job then lapses, two minutes after its last renewal by default, and the board shows the card in Stopped, marked stalled.

A stalled run cannot be carried on yet. Carrying on a run that stopped part-way needs [durable runs](roadmap.md#next), which are on the roadmap. Until then, a person presses "Start again" on the card. The board ends the old run with `factory.abandon` and starts the mission again as a new run. [Stalled runs on the board](operating.md#stalled-runs-on-the-board) says what that keeps and what it loses.
