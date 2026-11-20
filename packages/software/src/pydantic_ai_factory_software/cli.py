"""The ``factory`` command: run the software factory from a terminal.

    factory run "A to-do list web page where I can add tasks and tick them off."
    factory run --brief brief.md --offline --no-input
    factory resume <run id> --approve
    factory show <run id>

Exit codes: 0 the run passed, 1 it ended without passing, 2 the command was wrong,
3 the run is waiting for a person.
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic_ai.exceptions import UserError
from pydantic_ai.models import Model

from pydantic_ai_factory import (
    Answer,
    ApprovalRefused,
    Approve,
    Changes,
    FactoryError,
    LocalStore,
    Reject,
    Reply,
    Run,
)
from pydantic_ai_factory.methodologies import GoldfishReport

from . import offline_model
from ._design import Design, render_markdown
from ._factory import SoftwareFactory

PASSED, ENDED, USAGE, WAITING = 0, 1, 2, 3
DEFAULT_MODEL = "anthropic:claude-opus-5-5"

Ask = Callable[[str], str]
Say = Callable[[str], None]


class CommandError(Exception):
    """The command cannot be carried out as given. The message is for the person."""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factory", description="Run the software factory from a terminal."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    def store(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--store", type=Path, default=Path(".factory"), help="where runs and artifacts are kept"
        )

    def shared(command: argparse.ArgumentParser) -> None:
        command.add_argument(
            "--model",
            default=os.environ.get("FACTORY_MODEL", DEFAULT_MODEL),
            help=f"a Pydantic AI model name (default: FACTORY_MODEL, or {DEFAULT_MODEL})",
        )
        command.add_argument(
            "--offline", action="store_true", help="use a scripted model; no key needed"
        )
        store(command)
        command.add_argument(
            "--out", type=Path, default=Path("designs"), help="where approved designs are written"
        )
        command.add_argument(
            "--by", default=getpass.getuser(), help="who is deciding (default: your user name)"
        )
        command.add_argument(
            "--no-input", action="store_true", help="never ask; save the run when it waits"
        )
        answers = command.add_mutually_exclusive_group()
        answers.add_argument("--approve", action="store_true", help="approve without asking")
        answers.add_argument("--changes", metavar="NOTE", help="ask for changes, with a note")
        answers.add_argument("--reject", metavar="NOTE", help="reject, with a note")
        answers.add_argument("--answer", metavar="TEXT", help="answer the agent's question")

    run = commands.add_parser("run", help="start a run")
    run.add_argument("mission", nargs="?", help="the mission, as text")
    run.add_argument("--brief", type=Path, help="a text file holding the mission")
    shared(run)

    resume = commands.add_parser("resume", help="answer a run that is waiting")
    resume.add_argument("run_id")
    shared(resume)

    show = commands.add_parser("show", help="print what happened in a run")
    show.add_argument("run_id")
    store(show)
    return parser


def _factory(args: argparse.Namespace) -> SoftwareFactory:
    model: Model | str = offline_model() if args.offline else str(args.model)
    try:
        return SoftwareFactory(model)
    except UserError as error:
        raise CommandError(f"{error}. Add --offline to try it with no key.") from error


def _mission(args: argparse.Namespace) -> str:
    if args.brief is not None and args.mission is not None:
        raise CommandError("give the mission as text or with --brief, not both")
    if args.brief is not None:
        if not args.brief.is_file():
            raise CommandError(f"there is no brief at {args.brief}")
        text = args.brief.read_text(encoding="utf-8")
    elif args.mission is not None:
        text = str(args.mission)
    else:
        raise CommandError('give a mission, such as factory run "A to-do list web page"')
    if not text.strip():
        raise CommandError("the mission is empty")
    return text.strip()


def slug(title: str) -> str:
    """A file name for a design: its title in lower case, words joined by hyphens."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "design"


def _review_line(run: Run, store: LocalStore) -> str | None:
    """One line on how the waiting work was reviewed, read from its goldfish reports."""
    if run.waiting is None:
        return None
    step = run.latest(run.waiting.station)
    refs = [ref for ref in (step.evidence if step else ()) if ref.type == "GoldfishReport"]
    if not refs:
        return None
    final = max(
        (store.get_sync(ref, GoldfishReport).payload for ref in refs), key=lambda r: r.round
    )
    rounds = "1 round" if final.round == 1 else f"{final.round} rounds"
    left = len(final.open)
    ending = (
        "the last round was clean."
        if final.clean
        else f"{left} finding{'' if left == 1 else 's'} {'is' if left == 1 else 'are'} still open."
    )
    return (
        f"{run.waiting.station}: reviewed in {rounds} by {len(final.goldfish)} goldfish; {ending}"
    )


def _show_work(run: Run, store: LocalStore, say: Say) -> None:
    """Print the design a person is asked to decide on, in full, as it would be written."""
    waiting = run.waiting
    if waiting is None or waiting.artifact is None or waiting.artifact.type != "Design":
        return
    design = store.get_sync(waiting.artifact, Design).payload
    for line in render_markdown(design).rstrip().splitlines():
        say(line)
    say("")


def _show_waiting(run: Run, store: LocalStore, say: Say) -> None:
    waiting = run.waiting
    if waiting is None:
        return
    _show_work(run, store, say)
    line = _review_line(run, store)
    if line:
        say(line)
    if waiting.kind == "tool-calls":
        say(f"The {waiting.agent} agent is asking you something.")
        return
    if waiting.kind == "approval":
        say(f"Waiting for approval of {waiting.artifact}.")
        return
    say(f"{waiting.station} needs a person to decide, because:")
    for reason in waiting.reasons:
        say(f"  - {reason}")
    say(f"Waiting for a decision on {waiting.artifact}.")


def _questions(run: Run) -> list[tuple[str, str]]:
    """Each question an agent asked, as (tool call id, question)."""
    requests = run.waiting.requests if run.waiting else None
    if requests is None:
        return []
    return [
        (call.tool_call_id, str(requests.metadata.get(call.tool_call_id, {}).get("question", "")))
        for call in requests.calls
    ]


def _answer_calls(args: argparse.Namespace, run: Run, ask: Ask, say: Say) -> Reply | None:
    requests = run.waiting.requests if run.waiting else None
    if requests is None:
        return None
    if requests.approvals:
        raise CommandError(
            f"run {run.run_id} waits for a tool call to be allowed; answer it from Python"
        )
    answers: dict[str, str] = {}
    for call_id, question in _questions(run):
        say(f"Question: {question}")
        if args.answer:
            answers[call_id] = args.answer
            continue
        if args.no_input:
            return None
        answer = ""
        while not answer:
            answer = ask("Your answer: ").strip()
        answers[call_id] = answer
    return run.reply(by=args.by, results=requests.build_results(calls=answers))


def _decide(
    args: argparse.Namespace, run: Run, ask: Ask, say: Say
) -> Approve | Changes | Reject | None:
    if args.approve:
        return run.approve(by=args.by)
    if args.changes:
        return run.request_changes(by=args.by, note=args.changes)
    if args.reject:
        return run.reject(by=args.by, note=args.reject)
    if args.no_input:
        return None
    while True:
        choice = ask("Approve, ask for changes, or reject? [a/c/r] ").strip().lower()
        if choice in ("a", "approve"):
            return run.approve(by=args.by)
        if choice in ("c", "changes", "r", "reject"):
            note = ask("Say why, in a sentence: ").strip()
            if not note:
                say("A note is needed, so the next draft can act on it.")
                continue
            if choice.startswith("c"):
                return run.request_changes(by=args.by, note=note)
            return run.reject(by=args.by, note=note)
        say("Please answer a, c or r.")


# How ``factory show`` words each kind of answer.
_ANSWERED = {
    "approve": "approved",
    "changes": "changes requested",
    "reject": "rejected",
    "reply": "replied",
}


def _write_design(run: Run, store: LocalStore, out: Path) -> Path:
    if run.output_ref is None:
        raise CommandError(f"run {run.run_id} has no design to write")
    design = store.get_sync(run.output_ref, Design).payload
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{slug(design.title)}.md"
    path.write_text(render_markdown(design), encoding="utf-8")
    return path


def _carry_on(
    args: argparse.Namespace,
    factory: SoftwareFactory,
    run: Run,
    store: LocalStore,
    ask: Ask,
    say: Say,
) -> int:
    """Answer the run until it ends or must wait, then report how it went."""
    while run.waiting is not None:
        _show_waiting(run, store, say)
        answer: Answer | None
        if run.waiting.kind == "tool-calls":
            answer = _answer_calls(args, run, ask, say)
        else:
            answer = _decide(args, run, ask, say)
        # A flag answers once. If the run waits again, the person is asked, or it stops.
        args.approve, args.changes, args.reject, args.answer = False, None, None, None
        if answer is None:
            offline = " --offline" if args.offline else ""
            say(f"Run {run.run_id} is waiting. It is saved in {args.store}.")
            flag = '--answer "YOUR ANSWER"' if run.waiting.kind == "tool-calls" else "--approve"
            say(f"Answer it with: factory resume {run.run_id} {flag}{offline}")
            return WAITING
        run = factory.resume_sync(answer, store=store)

    say(f"Run {run.run_id} {run.outcome.replace('_', ' ')}.")
    if run.outcome != "passed":
        return ENDED
    say(f"Design written to {_write_design(run, store, args.out).as_posix()}")
    return PASSED


def _run(args: argparse.Namespace, ask: Ask, say: Say) -> int:
    mission = _mission(args)
    factory = _factory(args)
    store = LocalStore(args.store)
    run = factory.run_sync(mission, store=store)
    return _carry_on(args, factory, run, store, ask, say)


def _resume(args: argparse.Namespace, ask: Ask, say: Say) -> int:
    store = LocalStore(args.store)
    run = _load(store, args.run_id)
    if run.waiting is None:
        raise CommandError(f"run {run.run_id} is not waiting: it {run.outcome.replace('_', ' ')}")
    return _carry_on(args, _factory(args), run, store, ask, say)


def _load(store: LocalStore, run_id: str) -> Run:
    try:
        return store.get_run_sync(run_id)
    except FactoryError as error:
        raise CommandError(f"no run {run_id!r} is saved in {store.root}") from error


def _show(args: argparse.Namespace, say: Say) -> int:
    store = LocalStore(args.store)
    run = _load(store, args.run_id)
    say(f"Run {run.run_id} of factory {run.factory}: {run.outcome.replace('_', ' ')}")
    for step in run.steps:
        say(f"  {step.station} attempt {step.attempt}: {step.outcome.replace('_', ' ')}")
    for answer in run.answers:
        note = f": {answer.note}" if not isinstance(answer, Reply) and answer.note else ""
        say(f"  {_ANSWERED[answer.kind]} by {answer.by}{note}")
    _show_waiting(run, store, say)
    if run.waiting is not None:
        return WAITING
    return PASSED if run.outcome == "passed" else ENDED


def main(argv: Sequence[str] | None = None, *, ask: Ask = input, say: Say = print) -> int:
    """Run the command and return its exit code."""
    # Pydantic AI prints a start-up banner on first use; it has no place in this output.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    try:
        args = _parser().parse_args(argv)
    except SystemExit as stop:
        return USAGE if stop.code else PASSED
    try:
        if args.command == "run":
            return _run(args, ask, say)
        if args.command == "resume":
            return _resume(args, ask, say)
        return _show(args, say)
    except (CommandError, ApprovalRefused, FactoryError, UserError) as error:
        print(f"factory: {error}", file=sys.stderr)
        return USAGE
    except (EOFError, KeyboardInterrupt):
        print("\nfactory: stopped. A run that was waiting is still saved.", file=sys.stderr)
        return WAITING


def entry() -> None:
    """The console script."""
    raise SystemExit(main())
