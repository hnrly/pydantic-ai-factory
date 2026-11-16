"""The factory command runs the software factory from a terminal, with or without a person there."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic_ai import ModelMessage, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from pydantic_ai_factory_software import cli
from pydantic_ai_factory_software._offline import BLANK_RULE, offline_model, title_of
from pydantic_ai_factory_software.cli import ENDED, PASSED, USAGE, WAITING, main, slug

MISSION = "A to-do list web page where I can add tasks and tick them off."


class Terminal:
    """Collects what the command says, and answers its questions from a script."""

    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)
        self.asked: list[str] = []
        self.lines: list[str] = []

    def ask(self, question: str) -> str:
        self.asked.append(question)
        return self.answers.pop(0)

    def say(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def run_id(self) -> str:
        found = re.search(r"Run (\w+) is waiting", self.text)
        if found is None:
            raise LookupError(f"no waiting run in:\n{self.text}")
        return found.group(1)


def factory(terminal: Terminal, tmp_path: Path, *arguments: str) -> int:
    paths = ["--store", str(tmp_path / "store"), "--out", str(tmp_path / "designs")]
    return main([*arguments, *paths, "--by", "ada"], ask=terminal.ask, say=terminal.say)


def design_text(tmp_path: Path) -> str:
    return (tmp_path / "designs" / "to-do-list.md").read_text(encoding="utf-8")


def test_answering_a_writes_the_reviewed_design_to_a_file_named_after_it(tmp_path: Path) -> None:
    terminal = Terminal("a")

    code = factory(terminal, tmp_path, "run", MISSION, "--offline")

    assert code == PASSED
    review = terminal.lines.index(
        "design: reviewed in 2 rounds by 3 goldfish; the last round was clean."
    )
    assert re.fullmatch(
        r"Waiting for approval of Design@[0-9a-f]{12}\.", terminal.lines[review + 1]
    )
    assert terminal.asked == ["Approve, ask for changes, or reject? [a/c/r] "]
    assert (
        terminal.lines[-1] == f"Design written to {(tmp_path / 'designs').as_posix()}/to-do-list.md"
    )
    written = design_text(tmp_path)
    assert written.startswith("# To-do list\n")
    assert BLANK_RULE in written


def test_the_design_is_shown_in_full_before_a_person_is_asked_to_decide(tmp_path: Path) -> None:
    terminal = Terminal("a")

    factory(terminal, tmp_path, "run", MISSION, "--offline")

    review = next(i for i, line in enumerate(terminal.lines) if "reviewed in" in line)
    shown = "\n".join(terminal.lines[:review])
    assert terminal.lines[0] == "# To-do list"
    assert design_text(tmp_path).rstrip() in shown


def test_show_prints_the_design_a_waiting_run_is_asking_about(tmp_path: Path) -> None:
    first = Terminal()
    factory(first, tmp_path, "run", MISSION, "--offline", "--no-input")

    shown = Terminal()
    main(["show", first.run_id, "--store", str(tmp_path / "store")], say=shown.say)

    assert "# To-do list" in shown.lines
    assert BLANK_RULE in shown.text


def test_with_no_input_the_run_waits_and_is_approved_later_by_resume(tmp_path: Path) -> None:
    first = Terminal()

    assert factory(first, tmp_path, "run", MISSION, "--offline", "--no-input") == WAITING
    assert first.asked == []
    assert not (tmp_path / "designs").exists()
    assert f"factory resume {first.run_id} --approve --offline" in first.text

    later = Terminal()
    assert factory(later, tmp_path, "resume", first.run_id, "--approve", "--offline") == PASSED
    assert later.asked == []
    assert design_text(tmp_path).startswith("# To-do list\n")


def test_a_request_for_changes_goes_back_to_the_designer_with_the_note(tmp_path: Path) -> None:
    terminal = Terminal("c", "Each task has a delete button.", "a")

    code = factory(terminal, tmp_path, "run", MISSION, "--offline")

    assert code == PASSED
    assert terminal.asked.count("Approve, ask for changes, or reject? [a/c/r] ") == 2
    assert "Each task has a delete button." in design_text(tmp_path)


def test_a_change_or_rejection_without_a_note_is_asked_again(tmp_path: Path) -> None:
    terminal = Terminal("c", "", "x", "r", "Not now.")

    code = factory(terminal, tmp_path, "run", MISSION, "--offline")

    assert code == ENDED
    assert "A note is needed, so the next draft can act on it." in terminal.lines
    assert "Please answer a, c or r." in terminal.lines
    assert terminal.lines[-1].endswith(" failed.")
    assert not (tmp_path / "designs").exists()


def test_a_rejected_run_ends_without_a_design(tmp_path: Path) -> None:
    terminal = Terminal()

    code = factory(terminal, tmp_path, "run", MISSION, "--offline", "--reject", "Not needed.")

    assert code == ENDED
    assert not (tmp_path / "designs").exists()


def test_the_mission_can_come_from_a_brief_file(tmp_path: Path) -> None:
    brief = tmp_path / "brief.md"
    brief.write_text(MISSION + "\n\nTicked tasks stay ticked after a reload.\n", encoding="utf-8")
    terminal = Terminal()

    code = factory(terminal, tmp_path, "run", "--brief", str(brief), "--offline", "--approve")

    assert code == PASSED
    assert design_text(tmp_path).startswith("# To-do list\n")


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["run", "--offline"], "give a mission"),
        (["run", MISSION, "--brief", "brief.md", "--offline"], "not both"),
        (["run", "--brief", "missing.md", "--offline"], "there is no brief"),
        (["run", "   ", "--offline"], "the mission is empty"),
        (["resume", "nope", "--offline"], "no run 'nope'"),
        (["show", "nope"], "no run 'nope'"),
        (["fly"], None),
    ],
)
def test_a_wrong_command_exits_with_2_and_says_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], arguments: list[str], message: str | None
) -> None:
    store = ["--store", str(tmp_path / "store")] if arguments[0] != "fly" else []

    code = main([*arguments, *store], ask=Terminal().ask, say=Terminal().say)

    assert code == USAGE
    if message is not None:
        assert message in capsys.readouterr().err


def test_resuming_a_run_that_has_ended_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first = Terminal()
    factory(first, tmp_path, "run", MISSION, "--offline", "--no-input")
    factory(Terminal(), tmp_path, "resume", first.run_id, "--approve", "--offline")

    code = factory(Terminal(), tmp_path, "resume", first.run_id, "--approve", "--offline")

    assert code == USAGE
    assert "is not waiting: it passed" in capsys.readouterr().err


def test_show_prints_a_run_and_exits_with_its_state(tmp_path: Path) -> None:
    first = Terminal()
    factory(first, tmp_path, "run", MISSION, "--offline", "--no-input")
    store = ["--store", str(tmp_path / "store")]

    waiting = Terminal()
    assert main(["show", first.run_id, *store], say=waiting.say) == WAITING
    assert waiting.lines[0] == f"Run {first.run_id} of factory software: waiting"

    factory(Terminal(), tmp_path, "resume", first.run_id, "--approve", "--offline")
    done = Terminal()
    assert main(["show", first.run_id, *store], say=done.say) == PASSED
    assert "  approved by ada" in done.lines


def asks_first(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """The offline designer, except that it asks the person one question before drafting."""
    answered = any(
        isinstance(part, ToolReturnPart) and part.tool_name == "ask_person"
        for message in messages
        for part in message.parts
    )
    may_ask = "ask_person" in {tool.name for tool in info.function_tools}
    if may_ask and not answered:
        question = {"question": "Should ticked tasks survive a reload?"}
        return ModelResponse(parts=[ToolCallPart("ask_person", question, "q1")])
    return offline_model().function(messages, info)  # pyright: ignore[reportOptionalCall, reportReturnType]


@pytest.fixture
def curious(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "offline_model", lambda: FunctionModel(asks_first))


@pytest.mark.usefixtures("curious")
def test_an_agent_question_is_asked_in_the_terminal_and_the_answer_goes_back(
    tmp_path: Path,
) -> None:
    terminal = Terminal("Yes, they stay ticked.", "a")

    code = factory(terminal, tmp_path, "run", MISSION, "--offline")

    assert code == PASSED
    assert "The designer agent is asking you something." in terminal.lines
    assert "Question: Should ticked tasks survive a reload?" in terminal.lines
    assert terminal.asked[0] == "Your answer: "


@pytest.mark.usefixtures("curious")
def test_answer_answers_the_question_without_asking(tmp_path: Path) -> None:
    terminal = Terminal()

    code = factory(
        terminal, tmp_path, "run", MISSION, "--offline", "--answer", "Yes.", "--no-input"
    )

    assert code == WAITING
    assert terminal.asked == []
    assert "Waiting for approval of Design@" in terminal.text


@pytest.mark.usefixtures("curious")
def test_with_no_input_a_question_leaves_the_run_waiting(tmp_path: Path) -> None:
    terminal = Terminal()

    code = factory(terminal, tmp_path, "run", MISSION, "--offline", "--no-input")

    assert code == WAITING
    assert terminal.asked == []
    assert "Question: Should ticked tasks survive a reload?" in terminal.lines


@pytest.mark.usefixtures("curious")
def test_a_run_left_waiting_on_a_question_says_to_resume_it_with_an_answer(
    tmp_path: Path,
) -> None:
    terminal = Terminal()

    factory(terminal, tmp_path, "run", MISSION, "--offline", "--no-input")

    run_id = next(line.split()[1] for line in terminal.lines if line.startswith("Run "))
    assert f'factory resume {run_id} --answer "YOUR ANSWER" --offline' in terminal.text
    assert "--approve" not in terminal.text


@pytest.mark.parametrize(
    ("mission", "title", "file"),
    [
        (MISSION, "To-do list", "to-do-list"),
        ("The bug tracker app, for my team.", "Bug tracker", "bug-tracker"),
        ("Recipes!", "Recipes", "recipes"),
    ],
)
def test_the_offline_designer_names_the_design_after_the_mission(
    mission: str, title: str, file: str
) -> None:
    assert title_of(mission) == title
    assert slug(title) == file
