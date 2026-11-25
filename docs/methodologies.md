---
title: Methodologies
description: A methodology is a capability whose work the factory can check.
---

# Methodologies

A [capability](https://pydantic.dev/docs/ai/capabilities/) changes what an agent does: it can give the agent tools, instructions and hooks. A methodology is a capability that can also be checked. While the agent works, it records evidence of what it did. A [gate](gates.md) then reads that evidence and decides whether the method was really followed.

That is the whole difference. On an agent of its own, a methodology behaves exactly like any other capability. Inside a [station](stations.md), the factory stores its evidence, runs its gate, and stamps its name and version on every record. To compare a factory with a coding tool, or a methodology with doing without it, see [Measuring](measuring.md).

Methodologies here are established ways of working with a name and a history outside this library. A one-off check, such as "the design mentions blank tasks", is a gate, not a methodology.

## Use one

Attach it to an agent, like any capability.

```python
from pydantic import BaseModel
from pydantic_ai import Agent

from pydantic_ai_factory import MemoryStore, station
from pydantic_ai_factory.methodologies import ElephantGoldfish
from pydantic_ai_factory_software import DESIGN_GOLDFISH


class Design(BaseModel):
    summary: str
    acceptance_criteria: list[str]


designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    output_type=Design,
    instructions="Write a short design.",
    capabilities=[ElephantGoldfish(goldfish=DESIGN_GOLDFISH)],
)

mission = "A to-do list web page where I can add tasks and tick them off."
result = station(designer).run_sync(mission, store=MemoryStore())
print(result.outcome, result.decision.reasons)
#> passed ('round 2 was clean: 3 goldfish found nothing blocking',)
```

[Elephant Goldfish](elephant-goldfish.md), Google's method for design-driven development, ships with the library, and `DESIGN_GOLDFISH`, the reviewers for a software design, ship with the [software factory](software-factory.md). Run `designer` in a station and its designs cannot pass until fresh reviewers have read them and found nothing blocking.

`factory.methodologies()` lists each station's methodologies before anything runs.

## Write your own

This one is behaviour-driven development, as Dan North described it: every acceptance criterion is a scenario, "Given … when … then …", so anyone can check it.

```python
import re
from dataclasses import dataclass

from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry

from pydantic_ai_factory import MemoryStore, Methodology, Verdict, failed, passed, station


class Design(BaseModel):
    summary: str
    acceptance_criteria: list[str]


class ScenarioCheck(BaseModel):
    not_scenarios: list[str]


def is_scenario(criterion: str) -> bool:
    return re.match(r"(?is)given .+ when .+ then .+", criterion) is not None


@dataclass
class BehaviourDriven(Methodology):
    name = "behaviour-driven-development"
    version = "1"
    evidence = (ScenarioCheck,)

    def get_instructions(self):
        return "Write every acceptance criterion as: Given ..., when ..., then ...."

    async def after_output_process(self, ctx, *, output_context, output):
        not_scenarios = [c for c in output.acceptance_criteria if not is_scenario(c)]
        await self.record(ctx, ScenarioCheck(not_scenarios=not_scenarios))
        if not_scenarios:
            raise ModelRetry("Rewrite these as Given/When/Then: " + "; ".join(not_scenarios))
        return output

    def judge(self, output, evidence) -> Verdict:
        if evidence.of(ScenarioCheck)[-1].payload.not_scenarios:
            return failed("some acceptance criteria are not scenarios")
        return passed("every acceptance criterion is a scenario")


designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    output_type=Design,
    instructions="Write a short design.",
    capabilities=[BehaviourDriven()],
)

mission = "A to-do list web page where I can add tasks and tick them off."
result = station(designer).run_sync(mission, store=MemoryStore())
print(result.outcome, result.decision.reasons)
#> passed ('every acceptance criterion is a scenario',)
print(len(result.evidence), [str(m) for m in result.decision.methodologies])
#> 2 ['behaviour-driven-development@1']
```

The designer's first draft said "Blank tasks are not added.", which is not a scenario. The methodology asked it to try again in the same conversation, and recorded a `ScenarioCheck` each time. The station kept both, and its gate read the last.

The parts are ordinary Pydantic AI, plus three things:

| Part | What it is |
|---|---|
| `get_instructions`, `after_output_process` | Pydantic AI capability hooks. Here they tell the agent the method, and ask it to try again when it slips. |
| `name`, `version` | Stamped on every record. Change the version when the method changes. |
| `self.record(ctx, ...)` and `evidence` | What the methodology records while the agent works. Inside a station each type must be recorded, or the station did not run. |
| `judge` | Its gate: plain code over that evidence. |

`self.record` emits the evidence into the agent's event stream as an `EvidenceRecorded` event, so it is harmless on an agent run on its own.

Each round in which the methodology raises `ModelRetry` uses one of the agent's output retries. Set `output_retries` on the class to the number of rounds it needs; it is 1 unless you say otherwise. A station adds it to the agent's own budget, so the methodology's rounds and the agent's ordinary validation retries do not starve each other.

One methodology object serves every run. If yours keeps state during a run, return a fresh copy from `for_run()`, as for any Pydantic AI capability.
