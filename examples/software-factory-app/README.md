# The software factory app

This folder runs the software factory as a small service. A person starts a mission, the factory designs the software, and the person approves the design, asks for changes or rejects it. The work is done by a separate worker, so the web process never needs a model key.

It has two processes that share one SQLite file:

- **The web process** (`app/api.py`) is a FastAPI app. It lists runs, shows one run, checks an answer fits the run, and adds a job to a queue. It never runs the factory.
- **The worker** (`app/worker.py`) claims each job in turn and does it: it starts a run, or carries a waiting run on with a person's answer. It is the only process that needs a model key.

The file holds the factory's runs and artifacts, kept by the library's `SQLiteStore`, and a `jobs` table that this app owns. Stop either process and start it again: nothing that was saved is lost. A run that stopped part-way through a station, because its worker was killed, is shown as stalled. It cannot be carried on yet. [Operating a factory](https://hnrly.github.io/pydantic-ai-factory/operating/#stalled-runs) explains why.

The web interface comes next. Until then, use the API.

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
| `GET /api/runs/{run_id}` | One run: its state, steps, decisions, what it waits for, its output as Markdown and its latest job of each kind. |
| `POST /api/runs/{run_id}/answer` | Takes `{choice, by, revision, note, replies, approvals}`. Queues the answer, or returns `422` when it does not fit and `409` when the run has moved on. |
| `GET /api/jobs/{job_id}` | One job's state, and why it failed if it did. |

The name in `by` is recorded with every decision. It is a name, not a sign-in: anyone can type any name.

## Your own factory

`app/factory.py` is the only file that knows this is the software factory. Change its two functions, `build_factory` and `render_output`, and the worker and the API run your factory instead.

## Copying this folder

This folder reaches nothing outside itself. Inside the repository, `pyproject.toml` installs the library from the repository's own folders. In a copy, install the published packages instead:

```bash
uv sync --no-sources
```

## Checks

```bash
uv run ruff check .
uv run pyright
uv run pytest
```
