---
title: Elephant Goldfish
description: Google's method for design-driven development. The author keeps the whole conversation; fresh reviewers see only the work.
---

# Elephant Goldfish

Elephant Goldfish is Google's [method](https://github.com/google/elephant-goldfish-model) for design-driven development. The author, the elephant, remembers everything: the mission, the conversation, every decision. The reviewers, the goldfish, remember nothing. Each is a fresh agent that sees only the finished work and one standard. A goldfish cannot read what the author meant, only what the author wrote, so what it finds is what a real reader would find.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import MemoryStore, station
from pydantic_ai_factory.methodologies import ElephantGoldfish, GoldfishReport
from pydantic_ai_factory_software import DESIGN_GOLDFISH

designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    instructions="Write a short design.",
    capabilities=[ElephantGoldfish(goldfish=DESIGN_GOLDFISH, rounds=2)],
)

store = MemoryStore()
mission = "A to-do list web page where I can add tasks and tick them off."
result = station(designer).run_sync(mission, store=store)
print(result.outcome, result.decision.reasons)
#> passed ('round 2 was clean: 3 goldfish found nothing blocking',)

for ref in result.evidence:
    report = store.get_sync(ref, GoldfishReport).payload
    print(report.round, [(finding.id, finding.severity, finding.issue) for finding in report.findings])
    #> 1 [('r1-critique-1', 'Major', 'It does not say what happens when you press Enter on a blank task.')]
    #> 2 []
```

When the designer finishes a draft, the goldfish read it. Their findings go back to the designer in the same conversation, and it revises. This repeats until a round is clean or the rounds run out. Inside a [station](stations.md), each round's report is stored, and the station cannot pass unless the last round was clean for this exact draft.

`DESIGN_GOLDFISH` comes from the [software factory](software-factory.md): three goldfish, `comprehension`, `critique` and `readiness`. Above, the first draft did not say what happens to a blank task. The `critique` goldfish found that, the designer added it, and the second round was clean. The station's gate, `clean_final_round`, read both reports. Each is stamped `elephant-goldfish@1`, so runs with the method and without it can be [measured](measuring.md) against each other.

## The goldfish

Each goldfish has one job, and they run in three stages.

| Stage | Job |
|---|---|
| Comprehension | Explain the work back in its own words, from the work alone. A wrong paraphrase is a finding. |
| Critique | Find what is wrong or missing, against one standard. |
| Readiness | Say whether the next person could act on the work without asking anything. |

The core library knows nothing about software. The goldfish and their standards come from you, or from a package such as `pydantic-ai-factory-software`:

```python
from pydantic_ai_factory.methodologies import Goldfish, Standard

ACCESSIBLE = Standard("accessible", "Everything on the page works with a keyboard and a screen reader.")

page_goldfish = (
    Goldfish("newcomer", stage="comprehension", job="Explain what this does, in your own words."),
    Goldfish("access", stage="critique", job="Find anything a disabled person could not use.", standard=ACCESSIBLE),
)
```

When a round goes back to the author, each comprehension goldfish's paraphrase goes with it. If the paraphrase is not what the author meant, the work says something else, and the author fixes it.

Each goldfish is an agent named `goldfish-<name>`, so it shows up by that name in traces. It uses the author's model unless you pass `model=`.

## Findings and answers

Each finding is a `Blocker`, a `Major` or a `Nit`. A round is clean when no Blocker or Major is open; nits never block. Each finding has an id from its round, its goldfish and its place, such as `r1-critique-1`.

The author answers every finding: it fixes it, contests it with evidence, escalates it to a person, or leaves a nit alone. It contests with the `contest` tool and escalates with the `escalate` tool. Contested and escalated findings go to a person as an [escalation](approvals.md#escalation). Code never waves them through. So does anything still open when the rounds run out.

A goldfish that fails or times out did not run, and nor did the station. Its silence is never counted as a clean review.

## Scoping questions

Before drafting, the author may ask a person what the mission leaves open, such as whether ticked tasks should survive a reload. Each question waits for an answer like any [tool call](approvals.md#tool-calls). `max_questions`, three by default, caps them; set it to 0 to switch them off. A question asked mid-review keeps the review as it was: the round count, and any finding already contested or escalated, carry on once the person answers.

## Settings

| Setting | Default | Means |
|---|---|---|
| `goldfish` | required | The reviewers. |
| `rounds` | 2 | Rounds of review before what is left goes to a person. |
| `model` | the author's | The model the goldfish use. |
| `max_questions` | 3 | Questions the author may ask before drafting. |

The goldfish spend from the author's budget. Their requests and tokens count in the author's usage, and a limit given to the author's run, such as `usage_limits=UsageLimits(request_limit=10)`, stops them too.
