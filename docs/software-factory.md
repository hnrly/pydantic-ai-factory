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

done = software.resume_sync(run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

Before the run waited for you, a designer drafted the design and fresh reviewers read it under the [Elephant Goldfish](elephant-goldfish.md) method. The designer revised it until a round of review was clean. Only then were you asked.

To try it in Python with no model key, give it `offline_model()`, the scripted model the `--offline` option of the [command](#from-the-terminal) uses. It makes no network calls. Its designs show that the factory works, not how a real model behaves.

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory, offline_model

software = SoftwareFactory(offline_model())
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=MemoryStore())
print(run.waiting.output.title)
#> To-do list
```

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
| `repairs` | 2 | How many times a design that fails its gate, [`design_is_buildable`](design-station.md#the-gate), is sent back to the designer before a person decides. |
| `goldfish` | `DESIGN_GOLDFISH` | The reviewers. |
| `review_model` | `model` | The model the reviewers use. |
| `accept` | none | A function that reads [signals](missions.md#signals) and returns a mission, as for any factory. |

To change more than these allow, build the factory yourself from the same parts: a designer agent with the `ElephantGoldfish` capability, in a station with `approval=True`.

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

design: reviewed in 2 rounds by 3 goldfish; the last round was clean.
Waiting for approval of Design@37619731dd69.
Approve, ask for changes, or reject? [a/c/r]
```

It prints the design in full first, so you decide on what you have read. Answer `a`, and the same design is written to `designs/to-do-list.md`. `factory show <run id>` prints it again for a run that is waiting. Add `--offline` to try it with a scripted model and no key. Add `--no-input` to save the waiting run instead of asking, then decide later with `factory resume <run id> --approve`.

| Option | Means |
|---|---|
| `--brief FILE` | Read the mission from a file instead. |
| `--model NAME` | The model to use. The default is `FACTORY_MODEL`, or `anthropic:claude-opus-5-5`. |
| `--approve`, `--changes NOTE`, `--reject NOTE` | Decide without being asked. |
| `--answer TEXT` | Answer every question the designer asks with this text, without being asked. Asked in the terminal, type an option's number or your own words. When a question lets you pick several, type their numbers separated by commas, such as `1, 3`. |
| `--store FOLDER` | Where runs are kept. The default is `.factory`. |
| `--out FOLDER` | Where approved designs are written. The default is `designs`. |

`factory show <run id>` prints what happened in a run. The command exits with 0 when the run passed, 1 when it ended without passing, 2 when the command was wrong, and 3 when the run is waiting for you.
