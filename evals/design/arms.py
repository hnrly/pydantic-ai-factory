"""The arms of Software Factory Evals: the systems it compares on each case.

* ``factory``: the shipped ``SoftwareFactory``, whose Design station is reviewed by
  Elephant Goldfish, as a :class:`~pydantic_ai_factory.evals.FactorySystem`.
* ``factory-simple``: the same factory, whose Design station's method is Elephant Goldfish
  with no goldfish: the same designer, offered the same skills, with no review. Against
  ``factory`` it is the ablation.
* ``claude-code``: vanilla Claude Code, the framework's baseline, on the pinned model.
* ``scripted-<kind>``: the framework's four scripted systems, test fixtures that run only
  offline.

The factory arms read the case's repository, each run in its own Docker container, and
are built from the software package's public names only. Offline they run on the scripted
designer. Each passing run's ``Design`` is delivered as markdown, through the factory's
own ``render_markdown``. Nothing in the benchmark reads the document's shape.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.models import Model
from pydantic_ai_harness.ask_user import MAX_QUESTIONS

from pydantic_ai_factory import AskPerson
from pydantic_ai_factory.evals import (
    SCRIPTED_KINDS,
    Arm,
    ArmContext,
    Case,
    ClaudeCodeSystem,
    FactorySystem,
    FactSheet,
    ScriptedKind,
    SystemAdapter,
    docker_sandbox,
    scripted_system,
)
from pydantic_ai_factory.methodologies import ElephantGoldfish
from pydantic_ai_factory_software import Design, SoftwareFactory, design_station, render_markdown

from .scripted import scripted_designer
from .version import CAP_USD, CLAUDE_CODE_MODEL, DELIVER_DESCRIPTION, SANDBOX_IMAGE
from .workspace import overlay_of

FACTORY_ARM = "factory"
SIMPLE_ARM = "factory-simple"
CLAUDE_CODE_ARM = "claude-code"

SCRIPTED_PREFIX = "scripted-"
"""A scripted system's arm is its kind after this prefix, such as ``scripted-correct``."""


def designer_question_caps() -> tuple[int, int]:
    """The designer's cap on rounds of questions, and on questions in a round, for the run card.

    The Design station gives its designer ``AskPerson()`` with its defaults, so the cap on
    rounds is its default. A round is one ``ask_user_question`` call, which Pydantic AI
    Harness caps at ``MAX_QUESTIONS`` questions.
    """
    return AskPerson().max_rounds, MAX_QUESTIONS


def factory_arm(
    model: Model | str, workspace: AbstractCapability[Any] | None, *, review: bool
) -> SoftwareFactory:
    """The factory one arm runs, built from the software package's public names only.

    With ``review``, the shipped ``SoftwareFactory`` unchanged. Without it, the same
    factory whose Design station's method is Elephant Goldfish with no goldfish.
    """
    if review:
        return SoftwareFactory(model, workspace=workspace)
    simple = design_station(model, method=ElephantGoldfish(goldfish=()), workspace=workspace)
    return SoftwareFactory(model, workspace=workspace, design=simple)


def rendered(output: Any) -> str:
    """The design as markdown, through the factory's own template."""
    design = output if isinstance(output, Design) else Design.model_validate(output)
    return render_markdown(design)


def factory_system(model: Model | str, *, review: bool) -> FactorySystem:
    """The ``factory`` or ``factory-simple`` arm, on ``model``, in the sandbox image."""

    def build(run_model: Model, workspace: AbstractCapability[Any] | None) -> SoftwareFactory:
        return factory_arm(run_model, workspace, review=review)

    return FactorySystem(
        name=FACTORY_ARM if review else SIMPLE_ARM,
        model=model,
        build=build,
        sandbox=docker_sandbox(SANDBOX_IMAGE),
        render=rendered,
    )


def claude_code_system(**options: Any) -> ClaudeCodeSystem:
    """The ``claude-code`` arm: the framework's Claude Code baseline, with this suite's values.

    ``options`` pass through, so a test can run a fake program in its place.
    """
    return ClaudeCodeSystem(
        model=CLAUDE_CODE_MODEL,
        cap_usd=CAP_USD,
        image=SANDBOX_IMAGE,
        name=CLAUDE_CODE_ARM,
        deliver_description=DELIVER_DESCRIPTION,
        **options,
    )


def _factory_arm(review: bool):
    def build(case: Case, sheet: FactSheet, context: ArmContext) -> SystemAdapter:
        model = (
            scripted_designer(sheet, overlay=overlay_of(case)) if context.offline else context.model
        )
        return factory_system(model, review=review)

    return build


def _scripted_arm(kind: ScriptedKind):
    def build(case: Case, sheet: FactSheet, context: ArmContext) -> SystemAdapter:
        return scripted_system(kind, sheet)

    return build


def _claude_code_arm(case: Case, sheet: FactSheet, context: ArmContext) -> SystemAdapter:
    return claude_code_system()


SCRIPTED_ARMS: tuple[Arm, ...] = tuple(
    Arm(name=SCRIPTED_PREFIX + kind, build=_scripted_arm(kind), real=False)
    for kind in SCRIPTED_KINDS
)
"""The arms of the scripted systems. They are test fixtures and run only offline."""

ARMS: tuple[Arm, ...] = (
    *SCRIPTED_ARMS,
    Arm(name=CLAUDE_CODE_ARM, build=_claude_code_arm, offline=False, reads_files=True),
    Arm(name=FACTORY_ARM, build=_factory_arm(True), reads_files=True),
    Arm(name=SIMPLE_ARM, build=_factory_arm(False), reads_files=True),
)
"""Every arm, in the order an experiment runs them."""
