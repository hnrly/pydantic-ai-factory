---
title: Gates
description: How a gate judges a station's output from recorded evidence, and what happens when it cannot.
---

# Gates

A gate decides whether a station's output may move on. It is plain code: a function that reads recorded evidence and returns a verdict with its reasons. A model may produce the evidence, but a model never makes the decision. This page shows how to write a gate, what its decision records, and what happens when a gate cannot be evaluated.

## Write a gate

A gate is a function with a decorator. It takes the station's output as an [artifact](artifacts.md), plus the evidence, and returns a verdict.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact, Evidence, Verdict, evaluate_gate, failed, gate, passed


class Design(BaseModel):
    summary: str
    steps: list[str]


@gate(version="1")
def has_steps(output: Artifact[Design], evidence: Evidence) -> Verdict:
    if not output.payload.steps:
        return failed("the design lists no steps")
    return passed(f"{len(output.payload.steps)} steps listed")


design = Artifact.create(Design(summary="Split the bill evenly", steps=["add", "divide"]))
decision = evaluate_gate(has_steps, design, Evidence([design]))

print(decision.judgement, decision.reasons)
# > pass ('2 steps listed',)
```

You will rarely call `evaluate_gate` yourself. Attach the gate to a [station](stations.md) with `@station(gate=has_steps)` and the station evaluates it after every run.

Keep a gate pure. It should do no I/O and call no model. Anything slow or uncertain belongs in the station, which records the result as evidence for the gate to read.

## The three verdicts

| Helper | Meaning | What the station reports |
|---|---|---|
| `passed(*reasons)` | The work may move on. | `passed` |
| `failed(*reasons)` | The work was judged and is not good enough. | `failed` |
| `needs_approval(*reasons)` | The checks are satisfied, and a person must now decide. | `needs_approval` |

`failed` needs at least one reason. A failure nobody can act on is not useful, so `failed()` raises `ValueError`.

## Requiring evidence

A gate names the evidence it needs with `requires`. Evidence is found by its payload type.

```python
from pydantic import BaseModel

from pydantic_ai_factory import (
    Artifact,
    Evidence,
    NotEvaluated,
    Verdict,
    evaluate_gate,
    failed,
    gate,
    passed,
)


class Design(BaseModel):
    summary: str


class ReviewReport(BaseModel):
    blockers: list[str]


@gate(version="1", requires=[ReviewReport])
def no_open_blockers(output: Artifact[Design], evidence: Evidence) -> Verdict:
    blockers = evidence.one(ReviewReport).payload.blockers
    return failed(*blockers) if blockers else passed("the review found no blockers")


design = Artifact.create(Design(summary="Split the bill evenly"))
report = Artifact.create(ReviewReport(blockers=["no rounding rule"]))

decision = evaluate_gate(no_open_blockers, design, Evidence([design, report]))
print(decision.judgement, decision.reasons)
# > fail ('no rounding rule',)

without_report = evaluate_gate(no_open_blockers, design, Evidence([design]))
assert isinstance(without_report, NotEvaluated)
print(without_report.reason)
# > missing evidence: ReviewReport
```

`Evidence` has two lookups.

| Call | Returns |
|---|---|
| `evidence.of(ReviewReport)` | Every artifact with that payload type, in the order recorded. |
| `evidence.one(ReviewReport)` | The single one. Raises `LookupError` if there is none or more than one. |

## When a gate cannot be evaluated

Sometimes a gate cannot give an answer. Then `evaluate_gate` returns `NotEvaluated`, with a reason. It is never a pass and never a fail.

| Cause | Reason reported |
|---|---|
| Required evidence is missing | `missing evidence: ReviewReport` |
| The check raised | `the check raised LookupError: …` |
| The check returned something that is not a verdict | `the check returned bool, not a verdict` |

When evidence is missing, the check function is not called at all.

A station whose gate was not evaluated reports `did_not_run`. This is the rule that keeps a factory honest. A review that never happened must not look like a review that found nothing. And a tool that was unreachable must not look like work that was judged and failed, because failed work may be sent back for rework and an unreachable tool is not fixed by redoing the work.

## What a decision records

When a gate is evaluated, the result is a `GateDecision`.

| Field | Meaning |
|---|---|
| `gate` | The gate's name. |
| `criteria_version` | The version you gave the gate. |
| `judgement` | `pass`, `fail` or `needs_approval`. |
| `reasons` | The reasons the check gave. |
| `judged` | The ref of every artifact the gate was shown, the output first. |
| `methodologies` | The methodologies in force. |
| `decided_at` | When it was decided. |

A decision always names at least one artifact. Because it holds refs, you can always tell exactly which content was judged, by which rules.

Change the gate's `version` whenever its rules change. Two decisions with different criteria versions are not comparable, and the record should say so.

## Naming a gate

A gate takes its name from its function. Pass `name=` to choose another, or when the check is a lambda.

```python
from pydantic_ai_factory import gate, passed

always = gate(version="1", name="always")(lambda output, evidence: passed("nothing to check"))

print(always.name, always.criteria_version)
# > always 1
```
