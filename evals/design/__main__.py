"""The command: ``python -m evals.design``. It parses arguments and calls the experiment.

python -m evals.design --offline --split smoke
python -m evals.design --regrade --out evals-out
python -m evals.design --offline --regrade --out evals-out
python -m evals.design --build-image
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic_ai_factory.evals import SandboxError

from .suite import regrade, run_experiment
from .version import PINNED_MODEL, SANDBOX_IMAGE
from .workspace import build_sandbox_image


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command with ``argv``, and return its exit code."""
    parser = argparse.ArgumentParser(
        prog="python -m evals.design",
        description="Software Factory Evals: run systems on the cases and grade each run.",
    )
    parser.add_argument(
        "--offline", action="store_true", help="use scripted models and scripted systems only"
    )
    parser.add_argument("--split", choices=("smoke", "dev", "test"), default="smoke")
    parser.add_argument(
        "--arm",
        action="append",
        default=[],
        help="an arm to run; may be given more than once (default: every arm the mode can run)",
    )
    parser.add_argument("--repeat", type=int, default=1, help="runs for each case in each arm")
    parser.add_argument("--out", type=Path, default=Path("evals-out"), help="the run folder")
    parser.add_argument(
        "--model",
        default=None,
        help=f"the model for the factory arms on real models (default: {PINNED_MODEL})",
    )
    parser.add_argument(
        "--judge",
        choices=("real", "scripted"),
        default=None,
        help="the checklist judge (default: scripted offline, real otherwise)",
    )
    parser.add_argument(
        "--regrade", action="store_true", help="grade the runs in --out again and run nothing"
    )
    parser.add_argument(
        "--build-image",
        action="store_true",
        help=f"build the sandbox image, {SANDBOX_IMAGE}, and run nothing",
    )
    args = parser.parse_args(argv)
    if args.build_image:
        try:
            image_id = build_sandbox_image()
        except SandboxError as error:
            print(error)
            return 2
        print(f"Built {SANDBOX_IMAGE}: {image_id}")
        return 0
    if args.offline:
        # Offline runs use scripted models; Pydantic AI's first-run banner is only noise.
        os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    if args.regrade:
        return regrade(args.out, judge=args.judge or ("scripted" if args.offline else "real"))
    return run_experiment(
        args.arm,
        args.split,
        args.repeat,
        args.out,
        offline=args.offline,
        model=args.model,
        judge=args.judge,
    )


if __name__ == "__main__":
    sys.exit(main())
