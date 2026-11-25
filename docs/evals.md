---
title: Measuring factories and stations
description: An evaluation framework on Pydantic Evals that runs any factory, any station or a coding tool through the same ports, with a simulated stakeholder, and grades each run with plain code.
---

# Measuring factories and stations

A factory, a station or a methodology inside one is a claim: work done this way comes out better. `pydantic_ai_factory.evals` tests such a claim. It runs systems on cases whose requests leave facts out on purpose, lets each question a simulated stakeholder who holds those facts, and grades what each delivers with plain code. It knows nothing about what is delivered: a design, a plan or a build. A suite says that.

It is built on `pydantic-evals`, the evaluation library that comes with [Pydantic AI](https://pydantic.dev/docs/ai/). Install it with the `evals` extra:

```bash
pip install "pydantic-ai-factory[evals]"
```

## How a run works

A **case** is a request and a **fact sheet**. A system sees the request, and a workspace when the suite gives one. The fact sheet stays on the grader side. It holds the hidden facts, a canary and, for scored cases, a checklist. Every fact a delivery must get right carries a value nobody could guess, such as "47 days", so plain code can tell whether a delivery states it.

A system under test, called an **arm**, is run through three ports, and nothing else crosses:

| Port | What crosses |
|---|---|
| The brief | The request, unchanged, and the workspace's path, if there is one |
| The stakeholder | One to four questions in a call; one reply for each |
| Delivery | The work as text, with a status of `delivered` or `blocked`. A system with a typed output also hands that over, as JSON |

**The stakeholder.** No model writes a word a system sees. A small model, pinned by the suite, picks which facts a question asks about. Plain code then sends those facts' statements, word for word. A question about nothing on the sheet gets the sheet's fallback reply, and a request for sign-off gets its approval reply.

Every question, reply and delivery is written to an append-only event log. Graders read that log and the delivery, never what a system says about itself beyond its cost and how it stopped.

## Run one case

Four scripted systems come with the framework, as test fixtures: `correct`, `silent`, `asks-everything` and `leaks-canary`. They need no model, so they show a run end to end:

```python
from pathlib import Path

import anyio

from pydantic_ai_factory.evals import (
    Case,
    FactSheet,
    grade,
    run_case,
    scripted_picker,
    scripted_system,
)

sheet = FactSheet.model_validate(
    {
        "case_name": "tasks",
        "challenge_class": "critical-ambiguity",
        "canary": "canary-1234abcd5678",
        "fallback_reply": "I don't have anything to add on that.",
        "approval_reply": "I can't sign anything off here.",
        "scored": False,
        "facts": [
            {
                "id": "retention",
                "value": "47 days",
                "criticality": "critical",
                "obtainable": True,
                "held_by": "product owner",
                "statement": "Ticked tasks are kept for 47 days.",
                "direct_question": "How long are ticked tasks kept?",
                "keywords": ["kept"],
            }
        ],
    }
)
case = Case(name="tasks", request="Build a to-do page.")


async def run(kind: str):
    record = await run_case(
        scripted_system(kind, sheet),
        case,
        sheet,
        arm=f"scripted-{kind}",
        out=Path("evals-out"),
        picker=scripted_picker(sheet),
        cap_usd=0.50,
        time_limit_seconds=60.0,
    )
    return grade(record, sheet)


for kind in ("correct", "silent"):
    graded = anyio.run(run, kind)
    print(kind, graded.outcome, graded.turns)
    #> correct verified 1
    #> silent failure 0
```

Each run gets its own folder, `<out>/<arm>/<case>/<n>/`, with its event log and its record.

## How a run is graded

Each run gets one outcome. Where more than one applies, the first in this table wins. No model decides it.

| Outcome | When | Counted as |
|---|---|---|
| `infrastructure_error` | The harness, the program or a model provider failed, twice. A failed run is rerun once, the same for every arm | Failure, reported apart |
| `policy_failure` | The canary is in the delivered text or in any question | Failure |
| `budget_exhausted` | The run reached its spending cap or its time limit | Failure, reported apart |
| `failure` | No delivery, a critical fact left out, or a block on a case where nothing is unknowable | Failure |
| `correct_block` | On a nobody-knows case, the system asked at least one question and delivered as blocked | Success |
| `verified` | Delivered, with every obtainable critical value stated. A stakeholder's value must also have been released; a value in the environment need not | Success |

Each case belongs to a class: `critical-ambiguity`, where the system must ask; `in-the-environment`, where it must find a critical fact in its workspace without asking; `complete`, where nothing is missing; and `nobody-knows`, where it must ask, learn nobody knows, and stop.

**The checklist.** Stating every critical fact decides the outcome; it does not say whether the work took the right direction. A scored case's checklist holds three to six items. Pydantic Evals' own judge reads one item and the delivered text in each call, with the suite's instructions, and passes or fails it. Its reason must quote the passage that shows the item, and plain code keeps a pass only when that quote is an exact run of the text. Each verdict is stored beside the run with the hash of the text it read, so grading a run again calls no judge. Coverage sits beside the outcome and never changes it.

## Plug in a system

Any system can be measured. Write a class with a `name` and an `async run(brief, ports)` that asks through `ports.ask_stakeholder` and ends with `ports.deliver`. The contract imports nothing from the factory.
