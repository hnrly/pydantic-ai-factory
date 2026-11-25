"""Four scripted systems, written as plain code against the ports.

They are grader-side fixtures, not systems under test, so the rule that a system never
sees the fact sheet does not apply to them: each is built from the case's fact sheet. They
never read a file, call no model and report a cost of zero. Each one must get a known
result on each class of case; that is what "the harness catches it" means.

* **correct** asks the direct question of each critical stakeholder fact, and writes each
  environment fact's statement without asking. If a reply says nobody knows a fact, it
  delivers as blocked; otherwise it writes each reply into its text and delivers.
* **silent** asks nothing and delivers.
* **asks-everything** asks the direct question of every fact, then finishes as the
  correct one does.
* **leaks-canary** behaves as the correct one does, but copies the canary into its text.

The correct systems also write every checklist item into the text, word for word, and
the silent one writes none, so the scripted judge gives full and zero coverage.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, get_args

from ._contract import MAX_QUESTIONS_PER_CALL, Brief, Ports, RunReport, Status
from ._facts import Fact, FactSheet

Kind = Literal["correct", "silent", "asks-everything", "leaks-canary"]
KINDS: tuple[Kind, ...] = get_args(Kind)

ARM_PREFIX = "scripted-"
"""A scripted system's arm is its kind after this prefix, such as ``scripted-correct``."""


class ScriptedSystem:
    """One scripted system for one case."""

    def __init__(self, kind: Kind, sheet: FactSheet) -> None:
        if kind not in KINDS:
            raise ValueError(f"{kind!r} is not a scripted system; they are {', '.join(KINDS)}")
        self._kind: Kind = kind
        self._sheet = sheet

    @property
    def name(self) -> str:
        """The arm's name, such as ``scripted-correct``."""
        return ARM_PREFIX + self._kind

    async def run(self, brief: Brief, ports: Ports) -> RunReport:
        """Ask what the script says, then deliver."""
        if self._kind == "silent":
            await ports.deliver(f"# Delivery\n\n{brief.text}\n", "delivered")
            return _report()
        facts = self._sheet.facts
        if self._kind != "asks-everything":
            facts = tuple(
                f for f in facts if f.criticality == "critical" and f.source == "stakeholder"
            )
        replies: list[str] = []
        for start in range(0, len(facts), MAX_QUESTIONS_PER_CALL):
            chunk = facts[start : start + MAX_QUESTIONS_PER_CALL]
            answered = await ports.ask_stakeholder([f.direct_question for f in chunk])
            replies.extend(reply.text for reply in answered)
        text, status = self._finish(brief, replies)
        await ports.deliver(text, status)
        return _report()

    def _finish(self, brief: Brief, replies: Sequence[str]) -> tuple[str, Status]:
        unknown = [f for f in self._sheet.facts if not f.obtainable]
        blocked = any(_says(fact, reply) for fact in unknown for reply in replies)
        held = [f.statement for f in self._sheet.facts if f.source == "environment"]
        checklist = [item.item for item in self._sheet.checklist]
        lines = ["# Delivery", "", brief.text, "", *replies, *held, "", *checklist]
        if self._kind == "leaks-canary":
            lines += ["", self._sheet.canary]
        status: Status = "blocked" if blocked else "delivered"
        return "\n".join(lines) + "\n", status


def scripted_system(kind: Kind, sheet: FactSheet) -> ScriptedSystem:
    """The scripted system of ``kind``, built from the case's fact sheet."""
    return ScriptedSystem(kind, sheet)


def _says(fact: Fact, reply: str) -> bool:
    return fact.statement in reply


def _report() -> RunReport:
    return RunReport(cost_usd=0.0, input_tokens=0, output_tokens=0, end="finished")
