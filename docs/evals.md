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

Every question, reply and delivery is written to an append-only event log. Graders read that log and the delivery, never what a system says about itself beyond its cost and how it stopped.

## Plug in a system

Any system can be measured. Write a class with a `name` and an `async run(brief, ports)` that asks through `ports.ask_stakeholder` and ends with `ports.deliver`. The contract imports nothing from the factory.
