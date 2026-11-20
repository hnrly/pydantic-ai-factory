---
title: Roadmap
description: What Pydantic AI Factory can do today, what is being built next, and what it deliberately does not try to do.
---

# Roadmap

This page says what the library can do today and what comes next. The library is built in public, one station at a time. Each step ends with something you can run, and each has a write-up explaining the ideas behind it. Nothing below is a promise of a date.

## Today

The first delivery is complete. It is the core contract and one real station.

| You can | Read |
|---|---|
| Build a factory of your own, run a mission through it and approve the result | [Getting started](getting-started.md) |
| Run the software factory on a mission, from Python or a terminal | [The software factory](software-factory.md) |
| Declare typed stations that run agents, or none | [Stations](stations.md) |
| Judge their output with plain code over recorded evidence | [Gates](gates.md) |
| Join stations into a factory with bounded rework | [Factories and rework](factories.md) |
| Wait for a person to approve, settle an escalation or answer an agent's tool call, and resume from the store in another process | [Approvals and escalation](approvals.md) |
| Commit to a mission, or turn signals such as a bug report into one | [Missions and signals](missions.md) |
| Write a way of working as a capability that a station enforces | [Methodologies](methodologies.md) |
| Have work reviewed by fresh goldfish, revised in the author's own conversation | [Elephant Goldfish](elephant-goldfish.md) |
| See a run as a trace | [Observability](observability.md) |
| Score a methodology against doing without it | [Measuring a methodology](measuring.md) |
| Find the runs waiting for a person, from any process that shares the store | [Operating a factory](operating.md) |
| Run a factory behind a web page, with its work done by a separate worker | [Deploying a factory](deploying.md) |

What "complete" does not mean: the API may still change before 1.0. The getting-started path is run against a real model by hand before each release, with the `live` test marker. The evaluation set has been run against a real model once, on 16 November 2026.

## Next

Each of these is one stack of small pull requests and one write-up. Build, Quality and Release each have a [coming-soon page](software-factory.md#the-stations).

| Step | What it adds | What you will be able to run |
|---|---|---|
| **Build** | A second station: a coding agent that implements an approved design in an isolated workspace. | A design in, a working change and its tests out. |
| **Quality** | Checks and review as gates on the build, with rework from Quality back to Build. | A factory that fails a build whose tests do not pass and sends it back. |
| **Release** | A publisher that opens a stack of pull requests on a code host. | An approved change delivered for a person to merge. |
| **Durable runs** | Adapters that run a factory on a durable engine: a local one first, a server-based one second. | A run that survives a crash and carries on where it stopped. |
| **Learning** | Outcome signals fed back into intake, and methodologies compared on real work. | A factory whose results become its next work. |
| **A second factory** | A factory in another domain, built from the same core. | Proof of which parts are truly generic. |

A way to send missions, questions and approvals through a chat tool arrives alongside Build or Quality, as another source of signals.

## Not planned

Some things are left out on purpose.

- **Autonomy by default.** A person approves. The library records who, what and when. Stations that act without approval will always be a choice you make explicitly.
- **A workflow engine.** A factory runs in-process. Durable execution will come from adapters to engines that already exist. The optional `serve` module ships one simple queue, `SQLiteDispatcher`, and a worker for it, so that a factory can be served without writing a queue. It never retries, and it holds no workflow state beyond one row per job.
- **An agent framework.** Pydantic AI is the agent framework. This library is the layer above it.
- **Self-modifying factories.** A factory may propose a change to a methodology from what it has learned. It does not apply one without a person.

## How the work is done

Every change is a small pull request with its own tests and its own documentation page, so it can be read as a developer would read it. The [decisions](decisions.md) page records why the foundations are shaped as they are. [Contributing](contributing.md) explains how to take part.
