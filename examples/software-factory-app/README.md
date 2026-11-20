# The software factory app

This folder runs the software factory as a small service. A person starts a mission, the factory designs the software, and the person approves the design, asks for changes or rejects it. The work is done by a separate worker, so the web process never needs a model key.

It has two processes that share one SQLite file:

- **The web process** (`app/api.py`) is a FastAPI app. It lists runs, shows one run, checks an answer fits the run, and adds a job to a queue. It never runs the factory.
- **The worker** (`app/worker.py`) claims each job in turn and does it: it starts a run, or carries a waiting run on with a person's answer. It is the only process that needs a model key.

The file holds the factory's runs and artifacts, kept by the library's `SQLiteStore`, and a `jobs` table that this app owns. Stop either process and start it again: nothing that was saved is lost. A run that stopped part-way through a station, because its worker was killed, is shown as stalled. It cannot be carried on yet. [Operating a factory](https://hnrly.github.io/pydantic-ai-factory/operating/#stalled-runs) explains why.

[Deploying a factory](https://hnrly.github.io/pydantic-ai-factory/deploying/) explains why the app is shaped this way, and what to change when you deploy your own.

A small web interface, in `web/`, sits on top of the API. It shows the runs waiting for a person, every run, a box to start a new mission, and each run with the actions it allows.

## Try it offline

You need [uv](https://docs.astral.sh/uv/). Nothing here needs a model key. In this folder:

```bash
uv sync
uv run python -m app.worker --offline     # terminal 1: the worker, on a scripted model
uv run uvicorn app.api:app                # terminal 2: the web process, on port 8000
```

Then, in a third terminal, start a run, see it wait, and approve it:

```bash
curl -s localhost:8000/api/runs -H 'content-type: application/json' \
  -d '{"mission": "A to-do list web page where I can add tasks and tick them off.", "by": "Ada"}'
curl -s 'localhost:8000/api/runs?outcome=waiting&order=oldest'     # the inbox
curl -s localhost:8000/api/runs/RUN_ID                              # the design, as Markdown
curl -s localhost:8000/api/runs/RUN_ID/answer -H 'content-type: application/json' \
  -d '{"choice": "approve", "by": "Ada", "revision": REVISION}'
```

`REVISION` is the run's `revision` as you saw it. If the run has moved on since, the answer is refused with `409` and the run as it is now.

`--offline=asking` uses a scripted model whose designer asks one question first, so you can see an agent's question and answer it with `{"choice": "reply", "replies": {CALL_ID: "Yes."}, ...}`.

## The web interface

The interface is a Vite and React app in `web/`. You need Node 22 or later. Its look comes from `web/src/tokens.css`, an exact copy of the docs site's design tokens, so the folder builds on its own. Dark is the default; a button in the header switches to the light theme.

To work on it, run the worker and the web process as above, then in a third terminal:

```bash
cd web
npm ci
npm run dev        # http://localhost:5173, sending /api to the web process on port 8000
```

To serve it from the web process instead, build it once. The web process serves `web/dist` at `/` whenever that folder exists, so a person visits one address:

```bash
cd web
npm ci
npm run build      # writes web/dist; restart the web process and visit http://localhost:8000
```

Type your name in the header before you act. It is kept in your browser and recorded with every answer. It is a name, not a sign-in: anyone can type any name.

- **Inbox** lists the runs waiting for a person, longest wait first. It reloads every 10 seconds.
- **All runs** lists every run, newest change first. A stalled run has a badge and the time it last changed.
- **New mission** starts a run and opens its page.
- **A run's page** shows the mission, the output, the steps and the actions its wait allows: approve, ask for changes or reject a design; answer an agent's question; approve or deny a tool call that needs sign-off. Approve is hidden when an escalation offers no work. A stalled run offers only "Start again", which queues a new run with the same mission. A run made by an earlier version of the factory offers nothing. A queued or running run is checked every 2 seconds until it waits or ends.

After you act, the page follows your job every 2 seconds until it is done or failed. A failed job's reason is shown, and the run is reloaded. If someone answered first, the page says so and shows the run as it is now.

## Run it with a model

Only the worker needs a key. Give it the model's name and the key its provider reads:

```bash
FACTORY_MODEL=anthropic:claude-opus-5-5 ANTHROPIC_API_KEY=... uv run python -m app.worker
uv run uvicorn app.api:app
```

## Settings

Both processes read these from the environment:

| Setting | Default | What it does |
|---|---|---|
| `FACTORY_DB` | `factory.db` | The SQLite file both processes share. |
| `FACTORY_STALLED_MINUTES` | `10` | How long a running run, or a claimed job, may go without a change before it is shown as stalled. Set it above your slowest real step. |
| `FACTORY_MODEL` | none | The model the worker uses. Only the worker reads it. |

The worker also takes `--db` in place of `FACTORY_DB`. Stop it with Ctrl+C: it finishes the job in hand, then exits. Press Ctrl+C again to stop at once.

## The API

| Route | Does |
|---|---|
| `POST /api/runs` | Takes `{mission, by}`. Queues a start job and returns `{run_id, job_id}`. |
| `GET /api/runs?outcome=&order=&limit=` | Lists runs, each with its display state, and `more` when more matched than were returned. |
| `GET /api/runs/{run_id}` | One run: its state, steps, answers, what it waits for, its output as Markdown and its latest job of each kind. |
| `POST /api/runs/{run_id}/answer` | Takes `{choice, by, revision, note, replies, approvals}`. Queues the answer, or returns `422` when it does not fit and `409` when the run has moved on. |
| `GET /api/jobs/{job_id}` | One job's state, and why it failed if it did. |

The name in `by` is recorded with every answer. It is a name, not a sign-in: anyone can type any name.

## Your own factory

`app/factory.py` is the only file that knows this is the software factory. Change its two functions, `build_factory` and `render_output`, and the worker and the API run your factory instead.

## Copying this folder

This folder reaches nothing outside itself. Inside the repository, `pyproject.toml` installs the library from the repository's own folders. In a copy, install the published packages instead:

```bash
uv sync --no-sources
```

The interface needs nothing outside `web/` either. Its `web/src/tokens.css` is a copy of the docs site's; inside the repository, CI fails when the two differ.

## Checks

```bash
uv run ruff check .
uv run pyright
uv run pytest
```

And for the interface, in `web/`:

```bash
npm test             # component tests, against a faked API
npm run typecheck
npm run build
```
