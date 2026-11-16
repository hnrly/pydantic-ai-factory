---
title: Measuring a methodology
description: An evaluation set that runs the Design station with independent review and without it, and scores both with plain code.
---

# Measuring a methodology

A methodology is a claim: work done this way comes out better. A claim like that should be tested. This page describes an evaluation set that runs the same missions through the Design station twice, once with a designer agent that has the [Elephant Goldfish](elephant-goldfish.md) capability and once with the same agent without it, and scores both. It shows how to run it, what it measures, and how far the offline numbers can be trusted, which is not far.

It is built on `pydantic-evals`, the evaluation library that comes with [Pydantic AI](https://pydantic.dev/docs/ai/).

## Run it

```bash
uv run python -m evals.design --offline
```

```text
Design station, 4 missions, model: offline (scripted)

                                 with review       without    difference
CoversTheMission                        1.00          1.00         +0.00
EnoughCriteria                          1.00          0.29         +0.71
CriteriaTiedToTheMission                0.84          0.00         +0.84
SettlesTheUnsaid                        0.00          0.00         +0.00
attempts                                1.00          1.00         +0.00
rounds                                  2.00          0.00         +2.00
open_findings                           1.00          0.00         +1.00

These numbers come from a scripted model. They show the evaluation works,
not how a real model behaves. Run with --model for that.
```

Read the table as a trade. With the review on, designs had more acceptance criteria, and far more of them named something the mission asked for. Coverage of the mission did not change, and neither did the number of attempts. The price was two rounds of review per mission, inside the same conversation. `SettlesTheUnsaid` is 0 in both arms, because the scripted designer only restates the mission and never decides anything it leaves unsaid.

## How far to trust these numbers

Not far. They come from the scripted model behind `--offline`, which is written to leave acceptance criteria thin on its first draft and to fix them when a reviewer objects. So the offline result is true by construction. It proves that the evaluation runs end to end and that its scores can tell a thin design from a full one. It says nothing about real models.

For that, run it with a real model:

```bash
uv run python -m evals.design --model anthropic:claude-opus-5-5 --out results.json
```

That run has not been done yet. When it is, its numbers will be reported here with the model, the date and the cost, whichever way they point. If review does not earn its extra attempt on real designs, this page will say so.

Four missions is also a small set. Treat a difference of a few hundredths as noise until the set is larger and each mission is run more than once.

## What is measured

Every score comes from plain code reading the design. No model judges another model's work.

| Score | What it is | Range |
|---|---|---|
| `CoversTheMission` | The share of the mission's key words that appear somewhere in the design. | 0 to 1 |
| `EnoughCriteria` | Whether the design has as many acceptance criteria as the mission has things to check. | 0 to 1 |
| `CriteriaTiedToTheMission` | The share of acceptance criteria that name something the mission asked for. A criterion such as "it works" names nothing. | 0 to 1 |
| `SettlesTheUnsaid` | The share of the case's hidden checks that the design settles. A hidden check is something the mission leaves unsaid that a good design must decide. | 0 to 1 |
| `attempts` | How many times the station ran. Review happens inside one attempt, so this counts rework only. | 1 or more |
| `rounds` | How many rounds of goldfish review ran. | 0 or more |
| `open_findings` | How many Blocker or Major findings the goldfish raised on the way. | 0 or more |

These are crude on purpose. Counting key words cannot tell a good design from a wordy one. What it can do is catch a design that has dropped part of the mission, or whose acceptance criteria are too vague to check, and those are the failures independent review is meant to catch. A score a person can recompute by reading the design is worth more here than a subtle one nobody can check.

Each case names the words its mission turns on.

| Case | The mission turns on |
|---|---|
| `todo-list` | to-do, add, tick |
| `keep-ticked-tasks` | add, tick, reload |
| `delete-tasks` | add, tick, delete, blank |
| `due-dates` | add, due date, tick, overdue |

Each mission is the to-do list web page from [Getting started](getting-started.md), or a variation on it: ticked tasks that survive a reload, deleting tasks and never adding a blank one, and due dates with overdue tasks first. A design needs one acceptance criterion for each word its mission turns on.

## Hidden checks

The first three scores measure faithfulness: did the design keep what the mission said? A strong model keeps it with or without review, so those scores show what review costs but not what it adds. Review earns its keep on what the mission does not say. A fresh reader asks the questions the author forgot.

So each case also has hidden checks. The designer never sees them. Each one is a question the mission leaves open, with the patterns a statement must match to answer it. A design settles a check when one of its statements, a behaviour, an acceptance criterion or an exclusion, matches every pattern. Some checks also name patterns that dodge the question: "No deleting of tasks" does not say whether a ticked task can be deleted.

| Case | Hidden checks |
|---|---|
| `todo-list` | What happens to a blank task? Do tasks survive a reload? Can a ticked task be unticked? |
| `keep-ticked-tasks` | What happens to a blank task? What happens when saved tasks cannot be read or written? Do tasks keep their order after a reload? |
| `delete-tasks` | Does a task of only spaces count as blank? Can a ticked task be deleted too? Is deleting confirmed first, or undoable? |
| `due-dates` | Can a task be added without a due date? Is a task due today overdue? In what order are the overdue tasks shown? |

Patterns are crude. They can miss a design that answers a question in words nobody expected, and a test checks that no mission answers its own hidden checks. Read a low score next to the design before trusting it. The patterns were checked against a real design of the to-do list mission: every check that design settled was matched by the statement that settles it.

## How the two arms differ

Both arms use the same model, the same missions and the same designer instructions, in a one-station factory built like the software factory's [Design station](design-station.md). The only difference is one capability on the agent.

| | With review | Without review |
|---|---|---|
| The agent | `designer`, with `capabilities=[ElephantGoldfish(...)]` | `plain_designer`, the same agent with no capabilities |
| Methodologies in force | `elephant-goldfish@1` | none |
| A draft reaches a person | After a clean round, or as an escalation | Straight away |

In both arms the evaluation approves whatever reaches a person, and accepts every escalation, without reading it. That would be wrong in real use. Here it is the point: the scores measure what the factory would have put in front of someone. A question the designer asks is answered with "Decide for yourself, and say what you decided in the design."

Because every artifact and decision is [stamped](methodologies.md) with the methodologies in force, the two arms' records can be told apart in a store long after the run.

## Use it for your own methodology

The pattern is small. Build two agents, one with your methodology and one without. Write a task that runs a factory around the agent it is given and returns what it produced. Write evaluators that score that with plain code. Run the dataset once per arm.

```python
from dataclasses import dataclass
from functools import partial

from pydantic_ai import Agent
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import Evaluator, EvaluatorContext

from pydantic_ai_factory import Factory, MemoryStore, station
from pydantic_ai_factory.methodologies import ElephantGoldfish
from pydantic_ai_factory_software import DESIGN_GOLDFISH, Design

INSTRUCTIONS = "Write a short design."
designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    output_type=Design,
    instructions=INSTRUCTIONS,
    capabilities=[ElephantGoldfish(goldfish=DESIGN_GOLDFISH)],
)
plain_designer = Agent("anthropic:claude-opus-5-5", name="designer", output_type=Design, instructions=INSTRUCTIONS)


@dataclass
class SaysWhatABlankTaskDoes(Evaluator[str, Design]):
    def evaluate(self, ctx: EvaluatorContext[str, Design]) -> float:
        return 1.0 if "blank" in ctx.output.model_dump_json().lower() else 0.0


async def run_design(agent: Agent[None, Design], mission: str) -> Design:
    software = Factory("software", [station(agent, approval=True)])
    store = MemoryStore()
    run = await software.run(mission, store=store)
    done = await software.resume(run, run.approve(by="eval"), store=store)
    return done.output


dataset = Dataset(
    name="design",
    cases=[Case(name="todo-list", inputs="A to-do list web page where I can add tasks and tick them off.")],
    evaluators=[SaysWhatABlankTaskDoes()],
)
for arm, agent in (("with review", designer), ("without", plain_designer)):
    report = dataset.evaluate_sync(partial(run_design, agent), name=arm, progress=False)
    print(arm, report.averages().scores)
    #> with review {'SaysWhatABlankTaskDoes': 1.0}
    #> without {'SaysWhatABlankTaskDoes': 0.0}
```

`run_design` builds a one-station factory around the agent it is given, runs the mission and approves what comes back. With the goldfish, the designer was told its first draft never said what happens to a blank task, and fixed it; without them, nobody told it. `evals/design.py` in the repository is the complete version, with four missions, hidden checks and seven scores. Both agents share the name `designer`, so each arm's records look the same apart from the stamp.

To compare two versions of one methodology, give them different `version` values and run one arm with each. The stamps keep the records apart.
