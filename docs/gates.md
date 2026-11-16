---
title: Gates
description: A gate is plain code that judges a station's output. A model may produce evidence; it never makes the decision.
---

# Gates

A gate decides whether a station's output may move on. It is plain code: a function that reads the output, and any evidence the station recorded, and returns a verdict with its reasons. A model may write the work or the evidence. A model never decides the gate.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Artifact, Evidence, Factory, MemoryStore, Rework, Verdict, failed, gate, passed, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")


@gate(version="1")
def says_what_happens_to_blank_tasks(output: Artifact[str], evidence: Evidence) -> Verdict:
    if "blank" in output.payload.lower():
        return passed("the design says what happens to a blank task")
    return failed("the design never says what happens to a blank task")


design = station(designer, gate=says_what_happens_to_blank_tasks)
software = Factory("software", [design], rework=[Rework(design, limit=2)])
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=MemoryStore())
print([(step.attempt, step.outcome) for step in run.steps])
#> [(1, 'failed'), (2, 'passed')]
```

The first design did not mention blank tasks, so the gate failed it and the rework rule sent it back. The designer was told why, in its own conversation, and the second design passed.

Keep a gate pure: no I/O and no model calls. Anything slow or uncertain belongs in the station, which records the result as evidence for the gate to read.

## Verdicts

| Verdict | Means |
|---|---|
| `passed(*reasons)` | The work may move on. |
| `failed(*reasons)` | The work was judged and is not good enough. |
| `escalate(*reasons)` | Code must not settle this; a person must. See [escalation](approvals.md#escalation). |

Every verdict needs at least one reason.

A gate never asks for approval. A station that needs a person's sign-off says so with `approval=True`, and the person is asked only after every gate has passed.

## Evidence

A gate can require evidence the station records, by type.

```python
from pydantic import BaseModel

from pydantic_ai_factory import Artifact, Evidence, MemoryStore, StationContext, Verdict, failed, gate, passed, station


class TestRun(BaseModel):
    passed: int
    failed: int


@gate(version="1", requires=[TestRun])
def tests_pass(output: Artifact[str], evidence: Evidence) -> Verdict:
    run = evidence.one(TestRun).payload
    return passed("all tests pass") if run.failed == 0 else failed(f"{run.failed} tests failed")


@station(gate=tests_pass)
async def build(ctx: StationContext, design: str) -> str:
    return "Built index.html, but the tests never ran."


result = build.run_sync("One page, index.html.", store=MemoryStore())
print(result.outcome, result.reason)
#> did_not_run gate 'tests_pass' was not evaluated: missing evidence: TestRun
```

If the evidence is missing, the gate is not evaluated and the station did not run. A test suite that never ran must not look like one that passed.

Change a gate's `version` whenever its rules change, so records judged by the old rules can be told apart.
