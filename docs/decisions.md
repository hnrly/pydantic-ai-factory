---
title: Decisions
description: The design decisions behind Pydantic AI Factory, with the reason for each and what was rejected.
---

# Decisions

This page records the decisions that shape the library and why each was made. Where there was a real alternative, it says what was turned down. They were settled before any code was written, so that the foundations are designed whole. A decision here is not rewritten later. If one changes, a new entry replaces it and says so.

The words used below each have a page under Concepts, starting with [factories](factories.md).

## The core has no durability engine

**Decided.** A factory runs in-process. Durable execution arrives later as adapters: a local one first, a server-based one second.

**Why.** The first station must run with nothing but a model key. And the public types must not be shaped by one engine's rules.

**Rejected.** Building on one durability engine from the start. It would make every reader install that engine before running anything, and it would leak the engine's limits into the contract.

## A factory is declared data, run by one small runner

**Decided.** A factory is a named, ordered list of stations plus bounded rework. The library owns one runner for it. A graph library such as `pydantic-graph` may still be used inside a station that needs a state machine.

**Why.** There must be one orchestration mechanism, not one per concept. A factory that is plain data can later be mapped onto a durable workflow by an adapter.

**Rejected.** Making a graph the factory. A graph run keeps its state in memory, so resuming one after a crash needs an engine underneath, and that would bring an engine into the core. Also rejected: shipping stations with nothing to join them. Also rejected: a separate `Line` type between a factory and its stations. It was one more noun to learn before anything could run, and one route per factory is enough: two routes are two factories. Also rejected: registering stations on a factory with a decorator, as tools are registered on an agent. Stations are an ordered, typed chain, so their order is stated in one place.

## A station is a typed contract

**Decided.** A station has a typed input, a typed output, a function and a gate. It names the agents it runs, if any. An agent is optional. "Capability" means Pydantic AI's `AbstractCapability` and nothing else.

**Why.** A station with no agent must be first class. And one word must have one meaning.

**Rejected.** Making a station a capability attached to an agent: a station without an agent would have nothing to attach to. Also rejected: a second "capability" abstraction above Pydantic AI's.

## Definitions are frozen; running is a method that holds no state

**Decided.** `Factory`, `Station` and `Gate` are frozen declarations, and a `Methodology` keeps no per-run state outside `for_run()`. Running is a method that holds no state, as `Agent.run` is in Pydantic AI: `Factory.run` returns a serialisable record. That record may be waiting on an approval, and `Factory.resume` carries it on.

**Why.** State lives in the store, never in a live object. The store keeps artifacts, which are write-once, and runs, which are replaced by compare-and-swap after every step. A web app or a durability adapter can then take over without changing any type.

**Rejected.** Leaving runs to the application to save: every app would write the same load, save and lock code. Also rejected: a factory object that owns its runs and its store. It is friendlier at first glance, and it grows into one object that everything depends on.

## Stations run agents through the context

**Decided.** A station runs an agent with `ctx.run_agent(agent, prompt)`, and the station finds the agents it may run by reading the names its function refers to when it is declared. `@station(agents=[...])` names them explicitly where inference cannot see them. `ctx.run_agent` stores the agent's Pydantic AI `Conversation` as an artifact keyed by the agent's name, carries it on at a later attempt with what went wrong, records the evidence of each methodology on the agent, joins each methodology's gate to the station's, and turns deferred tool calls into a waiting run. It refuses an agent the station did not name.

**Why.** An agent is the thing a station most often does, and an earlier draft left every station to wire it by hand: a fresh run on every attempt with no conversation carried, capabilities passed in by convention and review found by type. Naming the agents lets a factory list every agent and methodology before a run, and lets a resumed run check it is the same work.

**Rejected.** A required `agents=[...]` list on every station: it repeated what the code already said, and forgetting an entry only failed at run time. Inference reads the same names Pydantic AI reads when it infers an agent's name. Also rejected: leaving agent runs to station code, with capabilities handed in through the context. It kept the core small but made the flagship station the most complicated thing on the site. Also rejected: building the agent inside the station, which hides it from the factory.

## A mission is the one noun; signals are the advanced path

**Decided.** `Mission(objective, success_criteria, constraints)` is built directly, and a string becomes one. `Factory.run` takes the first station's input and nothing else. `Signal(payload, source=None)` remains for work that arrives from people and systems: the payload's type is the kind of signal, and intake returns a `Mission` or a `Declined`. A mission made by intake records its signals; one built by hand records none.

**Why.** One request passed through five types (`Request`, `Signal`, `Commit`, `Mission`, `Declined`) and two free strings (`kind="intent"`, `source="terminal"`) before any work began. Most callers just have a mission.

**Rejected.** Signals as the only way in, which made every first sample start with ceremony. Also rejected: a `kind` field beside a typed payload, which says the same thing twice. Also rejected, still: the word "intent", which named a vague ask and a measurable promise with one word.

## A run waits for a person in three ways

**Decided.** A waiting run has `run.waiting.kind` of `"approval"`, `"escalation"` or `"tool-calls"`. Approval is declared with `@station(approval=True)`, and the person is asked only when every gate has passed. Escalation comes from a gate's `escalate` verdict, a station raising `Escalate`, or `Rework(on_limit="escalate")`. Approvals and escalations are answered with `run.approve`, `run.request_changes` and `run.reject`, each bound to the digest of what is waiting. Tool-call waits come from Pydantic AI deferred tools inside `ctx.run_agent` and are answered with Pydantic AI's own `DeferredToolResults`.

**Why.** A sign-off is bound to an exact artifact, so it stays a factory decision, not a tool call. But an agent inside a station also needs to ask a person things, and before, a deferred tool call had nowhere to go. Three kinds, answered with three verbs or Pydantic AI's own type, give levels of approval without many options.

**Rejected.** A hand-written gate returning "needs approval", which every station had to repeat. Also rejected: keeping Pydantic AI's deferred tools out of the factory. They are how an agent waits for a person. Also rejected: more kinds of wait, or more verbs.

## A person's answer is one type that names its run

**Decided.** `run.approve`, `run.request_changes`, `run.reject` and `run.reply` each return an `Answer`: one of `Approve`, `Changes`, `Reject` or `Reply`, told apart by `kind`. An answer names its run, the revision the person saw, the wait it answers and the work on offer, who answered and when. `factory.check(run, answer)` says why an answer does not fit, and `factory.resume(answer, store=)` applies the same rules before it carries the run on. Tool-call waits are still answered with Pydantic AI's `DeferredToolResults`, wrapped in a `Reply` that names the run and who answered. `run.answers` records every answer, tool replies included. This replaces `Decision` and `run.approvals`.

**Why.** An answer came in two shapes, and neither named its run, so `resume` needed the whole `Run` to know which run and revision it was for. A web app had to invent its own answer type, and copy the core's checks, to queue an answer for a worker. Tool replies were left out of the run's history.

**Rejected.** Keeping `Decision` and `DeferredToolResults`, and adding a request model for web apps only: every deployment would carry a translation layer and its own copy of the checks. Also rejected: putting `check` on the run. A run cannot see the factory, so it cannot tell whether the factory's shape has changed.

## A methodology is a capability

**Decided.** `Methodology` subclasses Pydantic AI's `AbstractCapability`, and adds a name, a version, the evidence it records and a gate that judges it. It is attached to an agent with `capabilities=[...]`. On a bare agent it shapes behaviour only. In a station, `ctx.run_agent` stores its evidence, joins its gate to the station's and stamps its name and version on every record. Elephant Goldfish is one capability whose review loop runs inside the author's own run.

**Why.** An earlier draft had a `Methodology` data type, a separate `Review` capability found by type, a `review_output` call and a `review=False` switch: four things to learn for one idea. A capability is what Pydantic AI already calls a reusable way of working, and the stamp still makes methodologies measurable.

**Rejected.** Methodologies as data attached to stations, which left the agent unaware of the method it was held to. Also rejected: review as rework between attempts, which started the author from scratch each time and lost the conversation. Also rejected: middleware around a station's function, which hides the loop.

## The foundations are designed whole

**Decided.** The full core contract is designed before the first station is built, including the parts the first station barely uses.

**Why.** The first station should fill in a contract, not define one by accident.

## One repository, two distributions

**Decided.** `pydantic-ai-factory` is the generic core. `pydantic-ai-factory-software` holds software stations. The second depends on the first, never the reverse, and CI checks the rule.

**Why.** The claim that the core is generic should rest on a package boundary, not on discipline.

## The type is `Artifact`

**Decided.** The type is spelled `Artifact`, and these pages use "artifact" as a defined term. All other prose is British.

**Why.** It is what Python developers will type and search for, and code and prose should match.

## A store lists its runs, and the queue belongs to the deployment

**Decided.** Every store must list its runs. `list_runs` is part of the `RunStore` protocol, filtered by outcome or factory and sorted by when each run last changed. A queue of work, and the worker that takes from it, belong to the deployment, not the library.

**Why.** Every application that operates a factory needs to find the runs waiting for a person, and before, each had to keep its own index of run ids beside the store. A store that cannot list its runs is a store nobody can operate. A queue is different: teams already have one, and the right choice depends on where they deploy. The library gives every process what it needs to share the work instead: a store any process can open, `list_runs`, and `run_id=` to name a run before it starts. The example on [Deploying a factory](deploying.md) keeps its queue in a table of its own, in the same file as its store.

**Rejected.** A separate, optional protocol for listing, which would have kept stores written for 0.1 working but let a store opt out of being operated. Also rejected: a queued state on a run and a call to claim it in the library. That starts the library down the road to a workflow engine, which the [roadmap](roadmap.md#not-planned) rules out. Durable runs may revisit it.

## The core defines a Dispatcher, and `serve` ships one simple queue

**Decided.** This replaces the queue half of [A store lists its runs, and the queue belongs to the deployment](#a-store-lists-its-runs-and-the-queue-belongs-to-the-deployment). The runs half stands: a `Run` still has no queued state, and `RunStore` still has no claim. The core now defines `Dispatcher`, a protocol that queues a `Job` to start a mission or to apply an `Answer`, and reports on the jobs it holds. It has no claim, so a deployment's own queue, or a durable engine, can fill it in its own way. The library ships one dispatcher, `SQLiteDispatcher`, with a worker, `work`, in the optional `pydantic_ai_factory.serve` module. It keeps its jobs in a table of the same file as `SQLiteStore`.

**Why.** A web page that serves a factory must queue work for a worker, and every deployment was writing the same small queue. The example app kept one in a table of its own. A board that shows where the work is needs the queue's view too: which runs are about to start, which claims have lapsed, and which run replaced which. A seam in the core lets the web process use any queue without knowing which. One simple queue in `serve` means a factory can be served with nothing more than one SQLite file.

**Why this is still not a workflow engine.** `SQLiteDispatcher` never retries. A claim lasts for a lease that the worker extends while it works. A claim that lapses is reported as stalled and is never handed to another worker: a person decides whether to start the run again. It holds no workflow state beyond one row per job. Durable execution still comes from adapters to engines that already exist, and the same seam is where they go.

**Rejected.** A `JobQueue` protocol with put, claim, extend and finish in the core. A claim is how one queue works, and a durable engine has its own way, so the core leaves it out. Also rejected: re-claiming a lapsed job automatically. A job that half ran may have done work that is not safe to repeat.
