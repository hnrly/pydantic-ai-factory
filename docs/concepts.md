---
title: Concepts
description: The model behind Pydantic AI Factory, and the exact meaning of each word it uses.
---

# Concepts

This page explains what an AI factory is and defines every word the library uses. A factory is a repeatable production system: signals come in, validated outcomes go out, and outcomes feed back as new signals. The library gives that loop a small set of typed parts. Read this page first; every other page builds on these definitions.

None of these types ships yet. Each one lands in its own pull request, with its own page, and this page links to them as they arrive.

The page describes the design in the present tense. Where it says the library "enforces" or "records" something, read that as what each part does once it has landed.

## The loop

```mermaid
flowchart LR
    S["Signal"] --> I["Intake"]
    I -->|"commit"| M["Mission"]
    I -.->|"decline"| X["Declined"]
    M --> L["Stations, in order"]
    L --> O["Outcome"]
    O -.->|"emitted as a signal"| S
```

Work enters a factory one way only: as a **signal**. A signal is a piece of data from a person or a system. A request typed at a terminal is a signal. So is a failing health check.

**Intake** is the step that reads signals and decides. It either commits to a **mission** or declines, with a reason. A mission is what the factory promises to do, written so that success can be measured.

A mission runs through the factory's **stations**, in order. When the run ends, the **outcome** is emitted as a new signal. That closes the loop: what a factory produces can become what it works on next.

Intake is opt-in. A factory can also run a plain input with no mission. Nothing then records why the work was taken on, and no outcome signal is emitted.

## Two questions, kept apart

An agent saying "done" tells you very little. So every station answers two separate questions.

| Question | Who answers | Possible answers |
|---|---|---|
| Did it run? | The station, or the runner when required evidence is missing | It ran, or it did not run. |
| Did it pass? | The station's gate | Pass, fail, or needs approval. |

A **gate** is plain code. It reads recorded evidence and returns a decision with its reasons. A model may produce the evidence, for example a review report. A model never makes the decision.

Three rules follow, and the library enforces all of them.

1. **Missing evidence is "did not run", never a pass.** If a check could not run, the run stops and says so.
2. **"Did not run" never triggers rework.** Rework is for work that was judged and failed. An unreachable tool is not a failed design.
3. **A person approves an exact thing.** An approval is a named person's decision on one artifact. It names that artifact by its digest, a fingerprint of its content. If the artifact changes, the approval is void.

## Inside a factory

```mermaid
flowchart LR
    A["Input artifact"] --> ST["Station"]
    ST --> E["Output and evidence"]
    E --> G{"Gate"}
    G -->|"passed"| N["Next station"]
    G -->|"failed"| R["Rework, bounded"]
    R --> ST
    G -->|"needs approval"| H["A person decides"]
    H -->|"approves"| N
    H -.->|"rejects"| X["Run stops"]
```

Two exits are not drawn. If required evidence is missing, the run stops as "did not run". If rework reaches its limit, the run halts or carries on, as described below.

A **station** is one bounded job with a typed input and a typed output. It may use one agent, several agents, or none. A station that only runs a formatter is as much a station as one that drafts a design. A station's gate is where its output is judged.

What a station hands on is an **artifact**. An artifact is never edited. It carries a digest of its content, the station that produced it and the artifacts it was made from. A correction is a new artifact that names the one it replaces.

When a gate fails, the factory may send work back, a fixed number of times. This is **rework**. At the limit the run either halts or carries on, as the rework rule declares. If it carries on, the station is still recorded as failed; later stations simply get the chance to run.

## Methodology

A **methodology** is a way of working, written down as data. It has a name, a version and three parts.

| Part | What it does |
|---|---|
| Capabilities | The Pydantic AI capabilities it adds to a station's agents. |
| Required evidence | What a station must produce, such as a review report. |
| Gates | Plain code that judges that evidence. They run as part of the station's gate. |

A station's gate passes only if the station's own check and every methodology's gates pass. Because it can require evidence and add to a gate, a methodology is more than a prompt. Its name and version are stamped on every artifact and every gate decision. That record is what later lets two methodologies be compared on the same work.

## The vocabulary

These are the public types. Each will have its own page.

| Word | Meaning |
|---|---|
| Signal | The one typed way in: a piece of data from a person or a system. |
| Mission | What the factory commits to, with measurable success criteria and the signals it came from. |
| Factory | A named, ordered list of stations, with bounded rework, an intake and default methodologies. It holds no live state. |
| Station | One bounded job with typed input and output. An agent is optional. |
| Artifact | An unchangeable, content-addressed record of what a station handed on. |
| Gate | Plain code that judges recorded evidence and gives reasons. |
| Approval | A named person's decision on one exact artifact. |
| Methodology | A named, versioned way of working: capabilities, required evidence, gates. |
| Run | The serialisable record of one piece of work moving through a factory. It may be waiting on an approval. |

Some words are used in prose but are not types.

| Word | Meaning |
|---|---|
| Intake | The step that turns signals into a mission or declines them. It is a method of the factory, not a type. |
| Intent | A signal that a person wrote. It is a kind of signal, not a separate type. |
| Evidence | An artifact in the role of being judged by a gate. Evidence and artifacts share one record. |
| Outcome | The result of a run. It is emitted as a signal. |

Two words come from Pydantic AI and keep its meaning exactly.

| Word | Meaning |
|---|---|
| Capability | Pydantic AI's `AbstractCapability`: a reusable bundle of tools, hooks and instructions for an agent. |
| Agent | Pydantic AI's `Agent`. |

The spelling "artifact" matches the `Artifact` type. Everything else on this site uses British spelling.

## What a factory is not

- **Not an agent framework.** Pydantic AI is the agent framework. The factory is the layer above it that says what was produced, how it was judged and who approved it.
- **Not a workflow engine.** A factory runs in-process. Durable execution arrives later as adapters, so the types here are not shaped by any one engine.
- **Not autonomous by default.** A person approves. The library records who, what and when.
