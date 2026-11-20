---
title: Serving a factory as a board
description: How to serve any factory to other people from one short file, as a board of cards moving across its stations, with the work done by a separate worker.
---

# Serving a factory as a board

The board is a web page that shows a factory's work. Each run is a card, in Missions, then one column per station, then Done and Stopped. People add missions straight into the Missions column, and answer the cards that wait for them. One short file serves it, for any factory that starts from a mission.

This page shows that file, then explains what the board shows and what people can do on it. [Deploying a factory](deploying.md) explains why it is shaped as a web process and a separate worker. [Operating a factory](operating.md) covers the same work from code.

## One file serves a factory

Here is the whole service for a two-station factory. A drafter writes a report, a person approves it, and a checker checks its figures:

```python title="board.py"
from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, SQLiteStore, station
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, work


def build_factory(model: Model | str) -> Factory:
    drafter = Agent(model, name="drafter", instructions="Draft the report the mission asks for.")
    checker = Agent(model, name="checker", instructions="Check the draft's figures.")
    return Factory("reports", [station(drafter, name="draft", approval=True), station(checker, name="check")])


store = SQLiteStore("factory.db")
dispatcher = SQLiteDispatcher("factory.db")

app = board_app(build_factory, store, dispatcher)  # the web process: uvicorn board:app

if __name__ == "__main__":
    work(build_factory, store, dispatcher, model="anthropic:claude-opus-5-5")  # the worker
```

Run each half in its own terminal:

```bash
pip install "pydantic-ai-factory[web]"
uvicorn board:app          # the web process, on port 8000
python board.py            # the worker
```

Open <http://localhost:8000>. The first screen asks for your name. Then the board shows five columns: Missions, Draft, Check, Done and Stopped.

`build_factory` takes a model and returns the factory. The worker calls it once, with the real model. The web process calls it with a model that needs no key. The [software factory example](https://github.com/hnrly/pydantic-ai-factory/tree/main/examples/software-factory-app) is the same file, with `SoftwareFactory` as `build_factory`.

## Trying it in one process

The board's API lives under `/api`. This sample drives it as the page would, through FastAPI's `TestClient`, and runs the worker until no job is queued. It starts one mission, approves the draft, and sees the card reach Done:

```python
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, OutputFormat, SQLiteStore, station
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app, work


def build_factory(model: Model | str) -> Factory:
    drafter = Agent(model, name="drafter", instructions="Draft the report the mission asks for, in Markdown.")
    checker = Agent(model, name="checker", instructions="Check the draft's figures.")
    draft = station(drafter, name="draft", approval=True, output_format=OutputFormat.MARKDOWN)
    return Factory("reports", [draft, station(checker, name="check")])


store = SQLiteStore("factory.db")
dispatcher = SQLiteDispatcher("factory.db")
app = board_app(build_factory, store, dispatcher)
ada = {"X-Factory-Name": "Ada"}

with TestClient(app) as browser:
    board = browser.get("/api/board").json()
    print([station["name"] for station in board["shape"]["stations"]])
    #> ['draft', 'check']

    mission = {"mission": "A weekly report on the support queue."}
    run_id = browser.post("/api/runs", json=mission, headers=ada).json()["run_id"]
    card = browser.get(f"/api/runs/{run_id}").json()
    print(card["column"], card["state"])
    #> missions queued

    work(build_factory, store, dispatcher, model="anthropic:claude-opus-5-5", until_idle=True)
    card = browser.get(f"/api/runs/{run_id}").json()
    print(card["station"], card["state"], card["actions"])
    #> draft waiting ['approve', 'changes', 'reject']
    print([(stop["station"], stop["state"], stop["attempt"]) for stop in card["track"]])
    #> [('draft', 'here', 1), ('check', 'ahead', None)]
    print(card["output_format"])
    #> markdown
    print(card["output"])
    """
    # Support queue

    Tickets fell from 40 to 31 this week.
    """

    answer = {"kind": "approve", "revision": card["revision"]}
    browser.post(f"/api/runs/{run_id}/answer", json=answer, headers=ada)
    work(build_factory, store, dispatcher, model="anthropic:claude-opus-5-5", until_idle=True)
    card = browser.get(f"/api/runs/{run_id}").json()
    print(card["column"], card["state"])
    #> done passed
```

The card is in Missions from the moment the mission is queued. Once the worker has saved the run, the card moves to Draft, and its `track` says the work is at the draft station, on its first attempt. The drafter writes Markdown, so its station asks for `OutputFormat.MARKDOWN`, and the board shows the draft as a document. Without it, a string output is shown as text, as written. A station whose output is a model is shown as Markdown the library writes from the model's fields, and any station can ask for JSON. [Stations](stations.md#how-a-stations-output-looks) says more.

## The web process needs no model key

`board_app` builds the factory with Pydantic AI's `TestModel`, which calls no model. It only reads the factory's shape, checks answers against runs, and shows each station's output in its `output_format`. It never runs the factory, so it never needs a key, and never should have one.

So `build_factory` must not need a key just to build the factory. Building an `Agent` on a model name does not call the model, so this holds for ordinary factories.

`board_app` serves a factory whose first station takes a `Mission` or a `str`, because the board starts each run from a mission a person types. For any other factory it raises `BoardUnsupported`, naming the station.

## The worker

`work(build_factory, store, dispatcher, model=...)` is the worker. It is the only process that runs the factory, and so the only one that needs a model key. It builds the factory once, then takes the oldest queued job, does it, and takes the next:

- **A start job** runs a mission, under the run id the web process chose when it queued the job.
- **An answer job** carries a waiting run on with a person's answer.

While a job runs, the worker renews its claim on it every quarter of the `lease`, two minutes by default. Pass `deps=` to give every run what a script would pass it. The first Ctrl+C or SIGTERM lets the job in hand finish, then stops the worker. `until_idle=True` stops it once no job is queued, as in the sample above.

You can run several workers on one file. `SQLiteDispatcher` hands each job to only one of them.

## The columns and the cards

The board has a Missions column first, then one column for each station, in the factory's order, then Done and Stopped. Each column has a small grey dot beside its title; pink is kept for cards that need a person. Every run, and every mission queued but not yet started, is one card in exactly one column. A card glides only when it changes column, and keeps its place in a column however often it changes, unless the reader's system asks for less motion.

To add a mission, type it in the "Add a mission" box at the top of Missions and press Enter, or choose Add. Shift+Enter starts a new line. The card appears in Missions at once.

The run, once it is saved, decides where its card goes:

- **A run that passed** is in Done.
- **A run made by an earlier version of the factory**, before a station or rework rule changed, is in Stopped, marked as an earlier version. Nobody can answer it, but if it is still running or waiting, it can be started again on the new shape.
- **A run that waits for a person** is in the column of the station it waits at. The card says what it waits for and for how long.
- **A run that failed, or did not run,** is in Stopped, with the reason.
- **A mission whose run is not saved yet** is in Missions. It is marked queued while its start job waits for a worker, and starting once a worker has taken the job. When the worker saves the run, the card moves to that run's station.
- **A running run** is in the column of the station it is at, and its icon turns while it works. If a rework rule sent the work back, the card says how many of the allowed reworks it has used, such as "Rework 1 of 2".
- **A run or mission whose worker stopped** is in Stopped, marked stalled. That is a running run, or a mission with no saved run, whose job failed, or whose worker's claim lapsed because it stopped renewing it.

Cards are never dragged. A person acts on a card only where the factory asks them to. Done and Stopped show their latest 20 cards, and "See all" pages back through the rest, as a list. It is the board's only list.

## Opening a run

Choose a card to open its run. The run opens as a panel over the board, which stays in view, dimmed, beside it. The address becomes `#/runs/` and the run's id, so a link to a run opens the board with that panel open. Escape, the close button or the browser's back button closes the panel and puts you back on the card.

From the top, the panel shows:

- **The run's state**, in words and a shape, such as "Needs your approval", "An agent asks you", "Stalled", "Running" or "Done", with how long it has waited, or when it ended, and who started it and when.
- **The mission**, as the headline.
- **The station track**: Mission, then every station of the factory in order, then Done. It marks the station the work is at, or stopped at, and which attempt it is on, such as "attempt 2" after a rework.
- **The one thing to do**, if there is one. For work waiting for approval, that is Approve, Ask for changes or Reject; the last two each open a box for your note first. For an agent's question, it is the question as a message, a box for your reply, and any tool calls the agent needs signed off, with Allow and Deny; the reply and the sign-offs are sent together. For a stalled run, it says why the run stalled and offers Start again, with its warning, or Keep waiting, which closes the panel.
- **The work itself**, as a readable document: the station's Markdown, or formatted JSON.
- **The history**, beside the work, or below it on a phone, newest first. It lists the gate's decisions with their reasons, each review round its evidence shows, each answer with who gave it and when, each station attempt, who added the mission, and, for a run started again, who did it, with a link to the new run. A folded "Technical detail" holds the run's id, its revision and the factory's version.

A run that has ended shows the same panel, without a thing to do: its output, the track ending at Done or where it stopped, and its history. `GET /api/runs/{id}` returns everything the panel shows, `track` and `history` included. An event whose time the run's records do not hold has no time, rather than a guessed one.

## Answering

A card that waits for a person offers, in its panel, the answers its wait allows: approve, ask for changes or reject, or reply to an agent's question. Anyone may answer; there is no assignment. A card that needs a person has a pink edge. The header counts those cards; choose the count, and the board scrolls to the card that has waited longest and flashes it.

The page sends only what the person decided, and the revision of the run they saw. The web process builds the answer from the run as it is in the store, so the station, the wait and the work always come from the run. It then checks the answer with [`factory.check`](approvals.md#answers), and queues it for the worker:

- **The answer fits.** It is queued, and the card is marked queued until the worker has applied it. A queued card is out of "Needs you".
- **Someone answered first.** The run has moved on since the revision the person saw. The request gets `409`, with the run as it is now, and nothing is queued.
- **The answer does not fit**, such as a reply that leaves a question unanswered. The request gets `422`, with the reason.

If two people answer before the worker has applied either, both answers are queued. The worker applies the first, and the second fails with "Someone answered first". Only one answer is ever applied.

## Start again

A card in Stopped offers "Start again", once. It queues a new run of the same mission, which appears in Missions as queued. The old card stays in Stopped, linking to the new run, with no actions.

A stalled card's run may still be saved as running, because its worker died part-way through a station. Start again first ends that run with [`factory.abandon`](factories.md#ending-a-run-that-is-stuck), as `"did_not_run"` with the reason "Started again by" and the person's name. The run's panel warns that the worker may only be slow, and offers Keep waiting instead. If it is, its next save fails, because the run has moved on, and nothing it does reaches the store.

Three things to know:

- **Start again begins at the first station.** The new run does not carry on where the old one stopped. Carrying on a run needs [durable runs](roadmap.md#next).
- **Earlier answers stay on the old run.** If a worker died while applying an answer, the old run records that answer, and the new run starts without it.
- **A slow worker can still create a duplicate run.** A start job whose worker stalled before it saved any run has nothing to conflict with. If that worker was only slow, it saves its run when it finishes, beside the one started again.

A run that waits for a person is not started again. To end it, reject it.

## Polling

The page asks `GET /api/board?since=` for changes every 3 seconds while its tab is visible. The server returns the cards that changed since `since`, and the `since` to send next. That is the time the poll started, less 10 seconds, so a save made during a poll, or a little clock skew, is never missed. Running cards and open jobs come back on every poll, because a claim can lapse with no new save.

An open run's panel follows its latest job every 2 seconds, so the person sees their answer applied.

## Who is answering

Every start, answer and Start again records who did it. By default it is a name the person gives on the board's first screen, which asks for it before the board shows. The browser keeps it, the header shows it with a way to change it, and it is sent in the `X-Factory-Name` header. It is a name, not a sign-in: anyone can type any name, and the board says so. A request with no name gets `400`, "Type your name first".

To use a real sign-in, pass `who=`. It takes the request and returns the person's name, or `None` to refuse the request:

```python
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic_ai import Agent
from pydantic_ai.models import Model

from pydantic_ai_factory import Factory, SQLiteStore, station
from pydantic_ai_factory.serve import SQLiteDispatcher, board_app


def build_factory(model: Model | str) -> Factory:
    drafter = Agent(model, name="drafter", instructions="Draft the report the mission asks for.")
    return Factory("reports", [station(drafter, name="draft", approval=True)])


def signed_in(request: Request) -> str | None:
    return request.headers.get("X-Forwarded-User")  # set by the sign-in proxy in front


app = board_app(build_factory, SQLiteStore("factory.db"), SQLiteDispatcher("factory.db"), who=signed_in)
mission = {"mission": "A weekly report on the support queue."}

with TestClient(app) as browser:
    print(browser.post("/api/runs", json=mission).status_code)
    #> 400
    print(browser.post("/api/runs", json=mission, headers={"X-Forwarded-User": "ada"}).status_code)
    #> 202
```

## The `[web]` extra

The board needs FastAPI and Uvicorn, which come with the `[web]` extra:

```bash
pip install "pydantic-ai-factory[web]"
```

Without the extra, calling `board_app` raises an error that names it. `SQLiteDispatcher` and `work` need only the core, so the worker can run without it.

The board's page is built into the wheel, so serving it from an installed package needs no Node. Its source is in `web/board/` in the repository. In a checkout where the page has not been built, `/` names the command that builds it.

## Your own queue

The web process talks to its queue through the `Dispatcher` protocol: it queues starts and answers, and reads jobs back. `SQLiteDispatcher` is one dispatcher, kept in the same SQLite file as `SQLiteStore`, so it suits one machine. `board_app` takes any `Dispatcher`, so a queue that spans machines, or a durable engine, can fill the same seam without changing the board. But `work` drives only `SQLiteDispatcher`, so a dispatcher of your own needs a worker of your own, which claims its jobs and runs or resumes the factory. [Decisions](decisions.md#the-core-defines-a-dispatcher-and-serve-ships-one-simple-queue) says why the core defines the seam and ships one simple queue.
