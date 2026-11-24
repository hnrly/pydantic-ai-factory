"""The worker for the board in ``board.py``, with a choice of model. Run it from this folder:

    python .                    # the model named in FACTORY_MODEL
    python . --offline          # the software factory's scripted model: no key needed
    python . --offline=asking   # a scripted model whose designer asks a question first

It does the board's queued jobs until Ctrl+C, finishing the job in hand first. With
``--until-idle`` it stops once no job is queued.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model
from pydantic_ai_factory.serve import work
from pydantic_ai_factory_software import SoftwareFactory, offline_model

from asking import asking_model
from board import DB, dispatcher, store


def _model(offline: str | None) -> Model | str | None:
    if offline == "asking":
        return asking_model()
    if offline is not None:
        return offline_model()
    return os.environ.get("FACTORY_MODEL") or None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python .", description="Do the board's queued jobs.")
    parser.add_argument(
        "--offline",
        nargs="?",
        const="scripted",
        choices=("scripted", "asking"),
        help="use a scripted model, so no key is needed; --offline=asking asks a question first",
    )
    parser.add_argument("--until-idle", action="store_true", help="stop once no job is queued")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Start the worker. Returns 0 once it stops, or 2 if it cannot start."""
    # Pydantic AI prints a start-up banner on first use; it has no place in this output.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    args = _parser().parse_args(argv)
    model = _model(args.offline)
    if model is None:
        print("worker: set FACTORY_MODEL, or add --offline to try it with no key.", file=sys.stderr)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    name = model if isinstance(model, str) else model.model_name
    print(f"Worker started on {name}, with jobs from {DB}.", flush=True)
    try:
        work(SoftwareFactory, store, dispatcher, model=model, until_idle=args.until_idle)
    except UserError as error:
        print(f"worker: {error}. Add --offline to try it with no key.", file=sys.stderr)
        return 2
    print("Worker stopped.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
