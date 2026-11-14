---
title: The software factory
description: A ready-made factory that turns a mission into reviewed, approved software, one station at a time.
---

# The software factory

The software factory is a ready-made factory for building software, made from the same parts as any other [factory](factories.md). It lives in its own package, `pydantic-ai-factory-software`. Today it has one station, [Design](design-station.md). Build, Quality and Release follow.

```bash
pip install "pydantic-ai-factory-software[anthropic]"
```

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory

software = SoftwareFactory("anthropic:claude-opus-5-5")
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
print(run.waiting.output.title)
#> To-do list

done = software.resume_sync(run, run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

Before the run waited for you, a designer drafted the design and fresh reviewers read it under the [Elephant Goldfish](elephant-goldfish.md) method. The designer revised it until a round of review was clean. Only then were you asked.

## The stations

| Station | Does | Status |
|---|---|---|
| [Design](design-station.md) | Drafts a design, has it reviewed, and waits for a person to approve it. | Ready |
| [Build](build-station.md) | Builds the approved design in an isolated workspace. | Coming soon |
| [Quality](quality-station.md) | Checks the build and sends it back when it fails. | Coming soon |
| [Release](release-station.md) | Delivers the change as pull requests for a person to merge. | Coming soon |

## Options

| Option | Default | Means |
|---|---|---|
| `model` | required | The model that drafts the design. |
| `rounds` | 2 | Rounds of review before what is left goes to a person. |
| `repairs` | 2 | How many times a design that still has open questions is sent back to the designer before a person decides. |
| `goldfish` | `DESIGN_GOLDFISH` | The reviewers. |
| `review_model` | `model` | The model the reviewers use. |
| `accept` | none | A function that reads [signals](missions.md#signals) and returns a mission, as for any factory. |

To change more than these allow, build the factory yourself from the same parts: a designer agent with the `ElephantGoldfish` capability, in a station with `approval=True`.

`SoftwareFactory` is a `Factory`, so everything on the [factories](factories.md) page works on it, such as `software.agents()` and `software.intake_sync(...)`.
