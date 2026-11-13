---
title: Decisions
description: The design decisions behind Pydantic AI Factory, with the reason for each and what was rejected.
---

# Decisions

This page records the decisions that shape the library and why each was made. Where there was a real alternative, it says what was turned down. They were settled before any code was written, so that the foundations are designed whole. A decision here is not rewritten later. If one changes, a new entry replaces it and says so.

The words used below are defined on the [concepts](concepts.md) page.

## The core has no durability engine

**Decided.** A factory runs in-process. Durable execution arrives later as adapters: a local one first, a server-based one second.

**Why.** The first station must run with nothing but a model key. And the public types must not be shaped by one engine's rules.

**Rejected.** Building on one durability engine from the start. It would make every reader install that engine before running anything, and it would leak the engine's limits into the contract.

## A factory is declared data, run by one small runner

**Decided.** A factory is a named, ordered list of stations plus bounded rework. The library owns one runner for it. A graph library such as `pydantic-graph` may still be used inside a station that needs a state machine.

**Why.** There must be one orchestration mechanism, not one per concept. A factory that is plain data can later be mapped onto a durable workflow by an adapter.

**Rejected.** Making a graph the factory. A graph run keeps its state in memory, so resuming one after a crash needs an engine underneath, and that would bring an engine into the core. Also rejected: shipping stations with nothing to join them. Also rejected: a separate `Line` type between a factory and its stations. It was one more noun to learn before anything could run, and one route per factory is enough: two routes are two factories. Also rejected: registering stations on a factory with a decorator, as tools are registered on an agent. Stations are an ordered, typed chain, so their order is stated in one place.

## A station is a typed contract

**Decided.** A station has a typed input, a typed output, a function and a gate. It may also list capabilities for its agents. An agent is optional. "Capability" means Pydantic AI's `AbstractCapability` and nothing else.

**Why.** A station with no agent must be first class. And one word must have one meaning.

**Rejected.** Making a station a capability attached to an agent: a station without an agent would have nothing to attach to. Also rejected: a second "capability" abstraction above Pydantic AI's.

## Definitions are frozen; running is a method that holds no state

**Decided.** `Factory`, `Station`, `Gate` and `Methodology` are frozen declarations. Running is a method that holds no state, as `Agent.run` is in Pydantic AI: `Factory.run` returns a serialisable record. That record may be waiting on an approval, and `Factory.resume` carries it on.

**Why.** State lives in the record and the artifact store, never in a live object. A durability adapter can then take over without changing any type.

**Rejected.** A factory object that owns its runs and its store. It is friendlier at first glance, and it grows into one object that everything depends on.

## Signals come in; missions are committed to

**Decided.** `Signal` is the one typed way into a factory. An intent is a signal that a person wrote. Intake accepts or declines signals and produces a `Mission`, which records the signals it came from. An outcome is emitted as a signal. A mission is optional: a factory can run a plain input, and then nothing records why the work was taken on and no outcome is emitted.

**Why.** There is one way in, and every result traces back to what triggered it. The feedback loop is a real path through the types.

**Rejected.** Using "intent" for both the raw request and the commitment: a vague ask and a measurable promise would share one type. Also rejected: three chained types (signal, intent, mission), which adds a step and little else. Also rejected: making a mission compulsory. When a person asks and a factory runs, wrapping the request in a signal and a mission is ceremony. Intake earns its place when it really decides.

## The human gate is a gate outcome

**Decided.** A gate may answer "needs approval". The runner then stops and returns a pending run that names the artifact by its digest. The caller resumes with a decision.

**Why.** The sign-off is bound to an exact artifact, not to a tool call. The caller can be a terminal prompt today and a chat message later, with no change to the factory.

**Rejected.** Modelling the decision as an agent tool that needs approval, which forces an agent into the step. Pydantic AI's deferred tools remain the right choice for approvals inside an agent run.

## A methodology is a versioned bundle, stamped on every record

**Decided.** A methodology has capabilities, required evidence and gates. Its name and version are stamped on each artifact and gate decision. Review and repair is ordinary bounded rework, driven by the gate.

**Why.** A methodology that can demand evidence and shape a gate is more than a prompt. The stamp makes two methodologies comparable later without changing the record.

**Rejected.** Middleware that wraps a station's run function: it can express anything, but it hides the loop from the factory. Also rejected: a capability with a naming convention, which is a prompt bundle in practice.

## The foundations are designed whole

**Decided.** The full core contract is designed before the first station is built, including the parts the first station barely uses.

**Why.** The first station should fill in a contract, not define one by accident.

## One repository, two distributions

**Decided.** `pydantic-ai-factory` is the generic core. `pydantic-ai-factory-software` holds software stations. The second depends on the first, never the reverse, and CI checks the rule.

**Why.** The claim that the core is generic should rest on a package boundary, not on discipline.

## The type is `Artifact`

**Decided.** The type is spelled `Artifact`, and these pages use "artifact" as a defined term. All other prose is British.

**Why.** It is what Python developers will type and search for, and code and prose should match.
