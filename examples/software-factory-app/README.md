# The software factory, served as a board

This folder serves the software factory to other people, from one short file. A person types a mission into a web page. The factory designs the software, and the design waits on the board for a person to approve it, ask for changes or reject it. The work is done by a separate worker, so the web process never needs a model key.

[`board.py`](board.py) is the whole service, in 20 lines. Its heart is this:

```python
store = SQLiteStore(DB)
dispatcher = SQLiteDispatcher(DB)

app = board_app(SoftwareFactory, store, dispatcher)

if __name__ == "__main__":
    work(SoftwareFactory, store, dispatcher, model="anthropic:claude-opus-5-5")
```

It runs as two processes that share one SQLite file:

- **The web process**, `uvicorn board:app`, serves the board and its API. It reads runs and queues work. It never runs the factory.
- **The worker**, `python board.py`, takes each queued job in turn and does it: it starts a run, or carries a waiting run on with a person's answer. It is the only process that needs a model key.

[Serving a factory as a board](https://hnrly.github.io/pydantic-ai-factory/board/) explains the board: its columns, its cards and what a person can do on each. [Deploying a factory](https://hnrly.github.io/pydantic-ai-factory/deploying/) explains why the service is shaped this way.

## Try it offline

You need [uv](https://docs.astral.sh/uv/). Nothing here needs a model key.

In a checkout of the repository, build the board's page once first. It needs Node 22 or later. An install from PyPI ships the page already built, so a copy of this folder skips this step:

```bash
(cd ../../web/board && npm ci && npm run build)
```

Then, in this folder:

```bash
uv sync
uv run python . --offline          # terminal 1: the worker, on a scripted model
uv run uvicorn board:app           # terminal 2: the web process, on port 8000
```

Open <http://localhost:8000>, type your name in the header, and start a mission. Its card appears in the Design column as queued, then waits there for you. Open it, read the design, and approve it. The card moves to Done.

`python . --offline=asking` uses a scripted model whose designer asks one question first, so you can see an agent's question on a card and reply to it.

`python .` is [`__main__.py`](__main__.py): the worker of `board.py`, with a choice of model. It also takes `--until-idle`, which stops it once no job is queued.

## Run it with a model

Only the worker needs a key. Run `board.py` itself, which uses Claude, with the key its provider reads:

```bash
ANTHROPIC_API_KEY=... uv run python board.py
uv run uvicorn board:app
```

Or name any Pydantic AI model in `FACTORY_MODEL`, and run `python .`:

```bash
FACTORY_MODEL=anthropic:claude-opus-5-5 ANTHROPIC_API_KEY=... uv run python .
```

With no `FACTORY_MODEL` and no `--offline`, `python .` says how to start and stops.

## Settings

| Setting | Default | What it does |
|---|---|---|
| `FACTORY_DB` | `factory.db` | The SQLite file both processes share. |
| `FACTORY_MODEL` | none | The model `python .` uses. Only the worker reads it. |

Stop the worker with Ctrl+C. It finishes the job in hand, then exits. Press Ctrl+C again to stop at once.

## Your own factory

Put your own factory in place of `SoftwareFactory` in `board.py`. Pass a function that takes a model and returns your factory, such as `build_factory(model) -> Factory`. The web process calls it with a model that needs no key, only to read the factory's shape. The worker calls it with the real model. The factory's first station must take a `Mission` or a `str`.

## The board's interface

The page ships built inside the library's wheel, so serving it from an install from PyPI needs no Node. A checkout of the repository does not hold the built page; build it as in [Try it offline](#try-it-offline). Its source is in [`web/board`](../../web/board/) at the root of the repository. To work on it, you need Node 22 or later:

```bash
cd ../../web/board
npm ci
npm test
npm run build      # writes the library's src/pydantic_ai_factory/serve/_static/
```

## Copying this folder

Inside the repository, this folder uses two things outside itself: `pyproject.toml` installs the library from the repository's own folders, and the board's page is built from `web/board`. A copy needs neither. Install the published packages instead, which ship the built page:

```bash
uv sync --no-sources
```

## Checks

```bash
uv run ruff check .
uv run pyright
uv run pytest
```
