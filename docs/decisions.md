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

**Decided.** `run.approve`, `run.request_changes`, `run.reject` and `run.reply` each return an `Answer`: one of `Approve`, `Changes`, `Reject` or `Reply`, told apart by `kind`. An answer names its run, the revision the person saw, the wait it answers and the work on offer, who answered and when. `factory.check(run, answer)` says why an answer does not fit, and `factory.resume(answer, store=)` applies the same rules before it carries the run on. Tool-call waits are answered by the question or action itself, never by a call id: `run.reply("Yes.", by=...)` for one question, `run.allow` or `run.deny` for one action, and a mapping from each `Question` or `Action` to its answer when there are several. The run builds Pydantic AI's `DeferredToolResults` underneath and wraps it in a `Reply` that names the run and who answered. `run.answers` records every answer, tool replies included. This replaces `Decision` and `run.approvals`.

**Why.** An answer came in two shapes, and neither named its run, so `resume` needed the whole `Run` to know which run and revision it was for. A web app had to invent its own answer type, and copy the core's checks, to queue an answer for a worker. Tool replies were left out of the run's history.

**Rejected.** Keeping `Decision` and `DeferredToolResults`, and adding a request model for web apps only: every deployment would carry a translation layer and its own copy of the checks. Also rejected: putting `check` on the run. A run cannot see the factory, so it cannot tell whether the factory's shape has changed. Also rejected: asking callers to build `DeferredToolResults` with `build_results` and call ids. Every caller wrapped it in the same translation, and a call id is not something a person reads. Also rejected: verbs on the factory that answer and resume in one call. An answer must stay a value, so a web page can queue it for a worker.

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

## Pydantic AI Harness is part of the core

**Decided.** `pydantic-ai-factory` depends on Pydantic AI Harness, and any core layer may import it. The core uses the harness's capabilities where one fits, such as `Skills` for Agent Skills, rather than building its own. Harness extras that bring heavy dependencies, such as Coder, are optional extras of the core: `pip install "pydantic-ai-factory[coder]"`.

**Why.** People should be able to build their own factory, such as a software factory of their own, from the core and Pydantic AI without the opinionated software package. Asking a person and loading skills are core features, and the harness already provides them. A copy of our own would drift from it.

**Rejected.** Putting the harness behind a `methodologies` extra that only Elephant Goldfish needed. It kept the harness's capabilities out of the core layers that need them, such as asking a person in `stations`, and it needed a guarded import and tests that hide the harness.

## Asking a person is the harness's AskUser, with rounds

**Decided.** `AskPerson` is a thin wrapper over Pydantic AI Harness's `AskUser(answerer=None)`. The agent asks with `ask_user_question`, and each question has a header, options with the recommended one first, and `multi_select`. A person answers each question with a label, labels, or their own words, and the core sends the answers back with the harness's `ask_user_result`, after checking them with its `check_response`. `AskPerson` adds instructions on how to ask and a cap on rounds, `max_rounds`, which takes the tool away with `prepare_tools`. Elephant Goldfish brings an `AskPerson` by default, `ask=AskPerson()`, so its authors ask without anyone remembering to add it; `ask=None` turns it off. Every `AskPerson` has the id `ask_person`, so an agent has one: an `AskPerson` listed on the agent wins over the one a methodology brings, and two listed with different `max_rounds` are refused. An author with no review still asks with `AskPerson` alone.

**Why.** The harness already has the question schema, its validation, the deferred wait and the answer format. Our own `ask_person` tool repeated them, with one free-text question per call.

**Rejected.** Making people add `AskPerson` beside Elephant Goldfish themselves, which they would forget. Keeping our own `ask_person` tool and `Question` model, and a cap that counts questions rather than rounds, which refused an agent for asking its questions together.

## A contest that cites what anyone can check may settle a finding

**Decided.** The `contest` tool takes `cites`: `path:symbol` for code, or `field:name` for a part of the author's output. Code checks the citation: that the field is on the output, or that the path exists in the run's workspace. With `ElephantGoldfish(settle_contests=True)`, a contest whose citation checks out settles its finding without a person. A settled finding stays settled: a later finding from the same goldfish with the same `where` and `kind` is settled at once and is not sent to the author again. Escalations always go to a person. The core default is unchanged: `settle_contests=False`, so every contest still goes to a person unless you choose otherwise. This narrows the rule in [Elephant Goldfish](elephant-goldfish.md#findings-and-answers) that code never waves a contested finding through, for those who choose it.

**Why.** A contest that cites a file anyone can open, or a field anyone can read, should not stop a run for a question code can answer. Code checks the citation, so a model never decides the gate. Keeping the old default means other users see no change unless they choose it.

**Rejected.** Sending every contested finding to a person, as 0.1 does, for the software factory's own review. It stops a run for questions code can answer. Also rejected: a contest that settles without a citation code can check. Then a model would wave its own finding through.

## Elephant Goldfish uses its skills by default

**Decided.** The method's guidance is written as four Agent Skills that ship with Elephant Goldfish, in the core distribution: `design-interview` and `design-resolve` for the author, `goldfish-comprehension` and `goldfish-critique` for two goldfish. They speak of "the work" and "the request", never of software. The author is always offered its two through Pydantic AI Harness's `Skills`, and loads one when it needs it. `goldfish` defaults to `ElephantGoldfish.GOLDFISH`, comprehension and critique built from their skills with `Goldfish.from_skill`; each goldfish gets its skill's body as its instructions. `goldfish=()` means no review, and the author is still offered the skills. Elephant Goldfish offers only its own skills. This is the one core default that changes from 0.1, where `goldfish` was required.

**Why.** A person can read, review and change the method as documents, without touching Python, and share them with the plugins they came from. The method belongs to Elephant Goldfish, so a factory for briefs or reports gets the same interview and the same goldfish as the software factory. A goldfish whose whole job is one skill must not depend on a model choosing to load it, or it would review by no standard while the round still counts.

**Rejected.** Keeping the method as Python strings: it can only be read and changed in Python. Keeping the skills in the software package: the method would belong to one use of it. Letting each goldfish load its own skill on demand. A switch to turn the skills off: a team that passes its own goldfish gets exactly its own.

## A method belongs to a station, not a factory

**Decided.** The software package publishes `design_station(model, *, method=...)` and `design_review(model=None)`. `method` defaults to `design_review()`, Elephant Goldfish with the three design goldfish, `rounds=4`, `clean_rounds=2`, `settle_contests=True` and `to_text=render_markdown`; `None` gives a designer with no method. `SoftwareFactory(model, *, design=None, repairs=2, accept=None)` takes one keyword for each station, never a method. Its 0.1 keywords `rounds`, `goldfish` and `review_model`, and the attributes that copied its settings, are removed with no deprecation period: before 1.0 the better shape wins over compatibility.

**Why.** A factory will have several stations, each with its own review or none. A factory-wide method would either apply to every station or quietly to one. The same parts compose a factory of your own, `Factory("ours", [design_station(model), build])`, and the simple designer that Factory Evals compares, `design_station(model, method=ElephantGoldfish(goldfish=()))`, with nothing assembled by hand.

**Rejected.** `SoftwareFactory(model, method=...)`, one keyword that swaps the review: see Why.

## Skills sit on the designer

**Decided.** Skills are project context, so they sit on the designer, not inside the method. `design_station` gives the designer a plain `Skills` over the software package's `design-read-code` and `design-doc`, beside its method, so the designer is offered exactly four skills with review or without it. The third software skill, `goldfish-readiness`, builds the readiness goldfish. The method is attached before the designer's `Skills`, which has `duplicate_names="keep_first"`. Two `Skills` on one agent combine into one catalogue, Elephant Goldfish's included, and the first wins a name, so no later skill replaces a method skill. Goldfish never load the designer's skills.

**Why.** The software package's skills are not the method, and a designer without review still needs them. Pydantic AI Harness combines two `Skills` on one agent into one catalogue; this release checks that it does so with Elephant Goldfish's catalogue too, and that `keep_first` keeps the method's skill when a later catalogue has the same name. Attached the other way round, the run fails on the duplicate name.

**Rejected.** Routing the designer's skills through the method, `ElephantGoldfish(skills=...)`. It would need an off switch, and a separate function for a designer without review.

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

## The benchmark grades the design session through ports, with plain code

**Decided.** [Software Factory Evals](measuring.md) replaces the first evaluation set, the old `python -m evals.design` script, and now runs as `python -m evals.design` itself. A system is run through three ports, the brief, the stakeholder and delivery, and is never looked inside. Every hidden fact carries a value nobody could guess, so plain code decides whether a design states it. The stakeholder's replies are written in the case's fact sheet, and a small model only picks which ones a question asked for. The baseline is a real coding tool, vanilla Claude Code, run as it ships. Every arm designs from a repository mounted read-only in a container of its own, which holds nothing else. No design shape is required of any arm: each writes its design as it would, and a judge model reads a hidden checklist beside the outcome, checked by plain code against the document's own words, without ever deciding it. Every case is public. Each case has one simulated stakeholder for now.

**Why.** The first evaluation set answered every question with the same fixed reply, so it could not tell a good question from a bad one, and the design session is mainly its questions. Ports let any system be measured, the factory and a tool people already use, by the same rules. A value nobody could guess turns "did the answer reach the design" into a check a person can repeat. Picked replies mean no model writes a word a system sees, so a reply cannot hint. A container keeps the answers out of reach by construction. A required shape would grade the format, and the factory's own template is its feature, not the thing measured. Public cases let anyone rerun the number; the container, not secrecy, keeps a system from the answers.

**Rejected.** Keeping `evals/design.py` beside the new benchmark: two evaluations with different rules on one page, and its to-do scores stayed published. The comparison with and without independent review survives as the ablation, `factory` against `factory-simple`. Also rejected: a model that decides which facts a question asked for and so decides the grade, a model writing the stakeholder's replies, a summary of the repository in the brief instead of file tools, private test cases, and several stakeholder roles from the start.

## Factories and stations are measured by one framework, and each domain brings a suite

**Decided.** The evaluation framework lives in the core library as [`pydantic_ai_factory.evals`](evals.md), installed with the `evals` extra. It measures any factory or any station through one port contract, a simulated stakeholder and plain-code grading, on Pydantic Evals: each arm is a `Dataset`, each run is graded by an `Evaluator`, the checklist is read by Pydantic Evals' own judge, and the report evaluators give the success rate and reliability. A station is run inside a factory of that one station, so there is one resume loop. Claude Code is a baseline any suite can compare with. A domain brings a suite: its cases, its arms, its words and its scorecard. Software Factory Evals is the first, in `evals/design/`, and is never published. The framework's outcome names are generic, such as `verified` and `failure`, and a suite labels them.

**Why.** The first benchmark was written around a design document, so nothing in it could measure a Build or Quality station, or a factory of someone else's. Most of it was already generic: the ports, the stakeholder, the grading rules and the statistics. Keeping one framework means every suite is graded by the same rules, and the core still holds no software words.

**Rejected.** A separate distribution for the framework: one more package to version, for code that only makes sense beside the core. Keeping it unpublished in the repository: nobody could measure their own factory. Driving a station directly with a second resume loop: two loops to keep in step. Pydantic Evals' judge without the quote check: a pass that cites nothing in the document cannot be checked by plain code, so the rubric asks for a quote and the framework checks it.

