"""Fact sheets: what only the grader side knows about a case.

A case has two halves, joined only by its name. The system is shown the request and,
when the suite gives one, a workspace. The fact sheet, loaded only by the simulator and
the graders, holds the hidden facts, the canary and the checklist.

Every fact a delivery must get right carries a value nobody could guess, such as
"47 minutes", so plain code can tell whether a delivery states it. :func:`values_in` is
the one function that decides that.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

MIN_CHECKLIST_ITEMS = 3
MAX_CHECKLIST_ITEMS = 6
MAX_OBTAINABLE_CRITICAL_FACTS = 3
"""A critical-ambiguity case asks for at most this many facts, to keep it small."""

Criticality = Literal["critical", "low"]
Source = Literal["stakeholder", "environment"]
"""Who holds a fact: the stakeholder, or the workspace the system may read."""

ChallengeClass = Literal["critical-ambiguity", "in-the-environment", "complete", "nobody-knows"]
"""What a case tests: asking, reading the workspace, not asking, or stopping."""


class Fact(BaseModel):
    """One hidden fact of a case.

    ``statement`` is the one sentence the stakeholder says for it, with the value written
    exactly. ``value`` is ``None`` when nobody knows the fact, and the statement says so.
    ``direct_question`` is the plainest question that asks for it. ``keywords`` are what
    the scripted picker looks for. ``held_by`` is unused until there are several roles.
    A fact whose ``source`` is ``environment`` is in the workspace, not with a person.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    value: str | None
    criticality: Criticality
    obtainable: bool
    source: Source = "stakeholder"
    held_by: str
    statement: str
    direct_question: str
    keywords: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _check(self) -> Self:
        if not self.obtainable and self.criticality == "low":
            raise ValueError(f"fact {self.id!r}: a fact nobody knows must be critical")
        if self.obtainable and not self.value:
            raise ValueError(f"fact {self.id!r}: a fact that can be obtained needs a value")
        if not self.obtainable and self.value is not None:
            raise ValueError(f"fact {self.id!r}: a fact nobody knows has no value")
        if self.source == "environment" and self.criticality == "low":
            raise ValueError(f"fact {self.id!r}: only a critical fact can be in the environment")
        if self.source == "environment" and not self.obtainable:
            raise ValueError(f"fact {self.id!r}: a fact in the environment can be obtained")
        return self


class ChecklistItem(BaseModel):
    """One direction a good delivery takes for the case, in plain words.

    It names no heading or section. Only the checklist judge reads it.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    item: str


class FactSheet(BaseModel):
    """Everything the grader side knows about one case.

    It refuses, when built, a sheet that breaks the rules cases are written to. A sheet
    that is not ``scored``, such as one that exists to debug the harness, may have no
    checklist.
    """

    model_config = ConfigDict(frozen=True)

    case_name: str
    challenge_class: ChallengeClass
    facts: tuple[Fact, ...]
    canary: str
    fallback_reply: str
    approval_reply: str
    checklist: tuple[ChecklistItem, ...] = ()
    scored: bool = True

    @model_validator(mode="after")
    def _check(self) -> Self:
        self._check_facts()
        self._check_class()
        self._check_values()
        self._check_checklist()
        return self

    def _check_facts(self) -> None:
        ids = [fact.id for fact in self.facts]
        if len(ids) != len(set(ids)):
            raise ValueError("two facts share an id")
        critical = [f for f in self.facts if f.criticality == "critical"]
        if any(f.obtainable for f in critical) and any(not f.obtainable for f in critical):
            raise ValueError(
                "a critical fact that can be obtained cannot sit beside one nobody knows"
            )

    def _check_class(self) -> None:
        critical = [f for f in self.facts if f.criticality == "critical"]
        obtainable = [f for f in critical if f.obtainable]
        held = [f for f in self.facts if f.source == "environment"]
        match self.challenge_class:
            case "complete":
                if critical:
                    raise ValueError("a complete case holds no critical fact")
            case "critical-ambiguity":
                if not 1 <= len(obtainable) <= MAX_OBTAINABLE_CRITICAL_FACTS:
                    raise ValueError(
                        "a critical-ambiguity case has one to "
                        f"{MAX_OBTAINABLE_CRITICAL_FACTS} critical facts that can be "
                        f"obtained, not {len(obtainable)}"
                    )
            case "nobody-knows":
                if not any(not f.obtainable for f in critical):
                    raise ValueError("a nobody-knows case has a critical fact nobody knows")
            case "in-the-environment":
                if not held:
                    raise ValueError("an in-the-environment case has a fact in the environment")
                if any(f.source == "stakeholder" for f in critical):
                    raise ValueError(
                        "every critical fact of an in-the-environment case is in the environment"
                    )
        if held and self.challenge_class != "in-the-environment":
            raise ValueError("only an in-the-environment case has facts in the environment")

    def _check_values(self) -> None:
        values = [(f.id, f.value) for f in self.facts if f.value is not None]
        for one_id, one in values:
            for other_id, other in values:
                if one_id != other_id and one in other:
                    raise ValueError(
                        f"the value of {one_id!r} appears inside the value of {other_id!r}"
                    )

    def _check_checklist(self) -> None:
        items = self.checklist
        ids = [item.id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("two checklist items share an id")
        if any(not item.item.strip() or not item.id.strip() for item in items):
            raise ValueError("a checklist item is empty")
        if not self.scored:
            return
        if not MIN_CHECKLIST_ITEMS <= len(items) <= MAX_CHECKLIST_ITEMS:
            raise ValueError(
                f"a case has {MIN_CHECKLIST_ITEMS} to {MAX_CHECKLIST_ITEMS} checklist items, "
                f"not {len(items)}"
            )

    def fact(self, fact_id: str) -> Fact:
        """The fact with this id. Raises ``KeyError`` when there is none."""
        for fact in self.facts:
            if fact.id == fact_id:
                return fact
        raise KeyError(fact_id)


def load_fact_sheet(path: Path, case_name: str | None = None) -> FactSheet:
    """Read the fact sheet at ``path``, and check it is ``case_name``'s when one is given."""
    sheet = FactSheet.model_validate_json(path.read_text(encoding="utf-8"))
    if case_name is not None and sheet.case_name != case_name:
        raise ValueError(f"{path.name} holds the fact sheet of {sheet.case_name!r}")
    return sheet


def appears(value: str, text: str) -> bool:
    """Whether ``value`` appears in ``text`` as an exact run of characters.

    Case matters, nothing is normalised, and there are no aliases. A match does not count
    when the character just before it or just after it is a letter or a digit.
    """
    if not value:
        return False
    start = text.find(value)
    while start != -1:
        end = start + len(value)
        before = text[start - 1] if start > 0 else ""
        after = text[end] if end < len(text) else ""
        if not before.isalnum() and not after.isalnum():
            return True
        start = text.find(value, start + 1)
    return False


def values_in(text: str, sheet: FactSheet) -> set[str]:
    """The ids of the facts whose value appears in ``text``. Only graders use it.

    A fact nobody knows has no value and never matches.
    """
    return {f.id for f in sheet.facts if f.value is not None and appears(f.value, text)}


def canary_in(text: str, sheet: FactSheet) -> bool:
    """Whether the sheet's canary appears in ``text``, by the same rule as a value."""
    return appears(sheet.canary, text)
