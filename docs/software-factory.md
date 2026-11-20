---
title: The software factory
description: A ready-made factory that turns a mission into reviewed, approved software, one station at a time.
---

# The software factory

The software factory is a ready-made factory for building software, made from the same parts as the one on [Getting started](getting-started.md). It lives in its own package, `pydantic-ai-factory-software`. Today it has one station, [Design](design-station.md). Build, Quality and Release follow.

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

Before the run waited for you, a designer drafted the design and fresh reviewers read it under the [Elephant Goldfish](elephant-goldfish.md) method. The designer revised it until two rounds of review in a row were clean. Only then were you asked.

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
| `workspace` | none | The code to design against, read-only: a capability that supplies a workspace, such as `LocalWorkspace(path)`. It is passed to the Design station the factory builds; a `design` you pass keeps its own. See [Designing in an existing codebase](design-station.md#designing-in-an-existing-codebase). |
| `design` | `design_station(model, workspace=workspace)` | The [Design](design-station.md) station. |
| `repairs` | 2 | How many times a design that fails its gate, [`design_is_buildable`](design-station.md#the-gate), is sent back to the designer before a person decides. |
| `accept` | none | A function that reads [signals](missions.md#signals) and returns a mission, as for any factory. |

The factory takes one keyword for each station, never a method. A method belongs to a station: a factory will have several, each with its own review or none. Build and Quality will add `build=` and `quality=` with their stations.

## Your own Design station

`design_station(model, method=..., workspace=None)` builds the Design station, and `design_review(model=None, workspace=None)` builds its default method: [Elephant Goldfish](elephant-goldfish.md) with the three design goldfish, up to four rounds, two clean rounds in a row, contests that code checks settled, and drafts read as Markdown. `model` there is the goldfish's model; by default they use the designer's. `workspace` gives the designer, or the goldfish, read-only file tools over the code; the station builds its default review with its own workspace. Pass the station to `SoftwareFactory`, or put it in a factory of your own.

```python
from pydantic_ai_factory import Factory
from pydantic_ai_factory.methodologies import ElephantGoldfish
from pydantic_ai_factory_software import SoftwareFactory, design_station

model = "anthropic:claude-opus-5-5"
shipped = SoftwareFactory(model)
simple = SoftwareFactory(model, design=design_station(model, method=ElephantGoldfish(goldfish=())))
plain = SoftwareFactory(model, design=design_station(model, method=None))
ours = Factory("ours", [design_station(model)])

print([str(m.ref) for m in simple.methodologies()["design"]], plain.methodologies()["design"])
#> ['elephant-goldfish@2'] ()
print([agent.name for agent in ours.agents()["design"]])
#> ['designer']
```

`simple` keeps the same designer, its skills, `AskPerson` and the gate, and leaves out only the review. `plain` has no method at all.

### Deprecated in 0.2

These still work in 0.2, raise a `DeprecationWarning`, and go in 0.3.

| Deprecated | Use instead |
|---|---|
| `SoftwareFactory(model, rounds=..., goldfish=..., review_model=...)` | `SoftwareFactory(model, design=design_station(model, method=...))`. The old keywords are mapped onto `design_review()`, and cannot be given with `design=`. |
| The attributes `software.model`, `software.rounds`, `software.repairs`, `software.goldfish` and `software.review_model` | Keep what you passed, or read it from the Design station's method, `software.methodologies()["design"]`. |

`SoftwareFactory` is a `Factory`, so everything on the [factories](factories.md) page works on it, such as `software.agents()` and `software.intake_sync(...)`.

## From the terminal

The package installs a `factory` command that runs the same factory and asks for your decision.

```bash
factory run "A to-do list web page where I can add tasks and tick them off."
```

```text
# To-do list

A to-do list web page where I can add tasks and tick them off.
...

design: reviewed in 3 rounds by 3 goldfish; the last round was clean.
Waiting for approval of Design@37619731dd69.
Approve, ask for changes, or reject? [a/c/r]
```

When the designer asks a round of questions, the command prints each one with why it matters and its options, the recommended first, then asks for one reply to the whole round.

It prints the design in full first, so you decide on what you have read. Answer `a`, and the same design is written to `designs/to-do-list.md`. `factory show <run id>` prints it again for a run that is waiting. Add `--offline` to try it with a scripted model and no key. Add `--no-input` to save the waiting run instead of asking, then decide later with `factory resume <run id> --approve`.

| Option | Means |
|---|---|
| `--brief FILE` | Read the mission from a file instead. |
| `--model NAME` | The model to use. The default is `FACTORY_MODEL`, or `anthropic:claude-opus-5-5`. |
| `--approve`, `--changes NOTE`, `--reject NOTE` | Decide without being asked. |
| `--answer TEXT` | Answer the designer's round of questions without being asked. One reply answers the whole round. |
| `--store FOLDER` | Where runs are kept. The default is `.factory`. |
| `--out FOLDER` | Where approved designs are written. The default is `designs`. |

`factory show <run id>` prints what happened in a run. The command exits with 0 when the run passed, 1 when it ended without passing, 2 when the command was wrong, and 3 when the run is waiting for you.
