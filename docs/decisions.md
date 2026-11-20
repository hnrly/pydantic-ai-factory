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

## Elephant Goldfish sits behind the methodologies extra

**Decided.** Elephant Goldfish ships in `pydantic-ai-factory`, in `pydantic_ai_factory.methodologies`, behind an optional extra: `pip install "pydantic-ai-factory[methodologies]"`. The extra adds Pydantic AI Harness. Importing `pydantic_ai_factory.methodologies` without it fails at once with a message naming that command. Only `methodologies` may import the harness, and the import check enforces it. The software package depends on the extra, so its users see no change.

**Why.** The method's skills load through Pydantic AI Harness. The core should not require the harness of someone who writes their own methodology, and an extra keeps the import path `pydantic_ai_factory.methodologies` where it was in 0.1.

**Rejected.** A separate `pydantic-ai-factory-methodologies` distribution. It would be a third distribution to version and release in step, and it would move the import path, needing a shim and a deprecation for 0.1 users.

## Asking a person is its own capability, in rounds

**Decided.** `AskPerson` is the only source of the `ask_person` tool. Elephant Goldfish no longer adds it; an agent that should ask is given `AskPerson` beside its methodology, as the software factory's designer is. The tool asks in rounds. A round is one model response that calls it: up to four questions, each with why it matters and up to three options, the recommended one first. The run waits once for the round, and the person replies once. `AskPerson(max_rounds=3, max_per_round=4)` caps rounds, not questions.

**Why.** A designer without review must still be able to ask, so asking cannot belong to a review method. And a round spends a person's attention once: four questions answered together cost them one interruption, not four. Recommending an option first lets a person agree in a word where they agree, and spend their thought where they do not.

**Rejected.** Asking one open question at a time with the options unranked, as Google's Elephant Goldfish plugin does. It interrupts the person once per question and leaves them to weigh options the agent has already weighed. Also rejected: counting questions rather than rounds. Several calls in one response were each counted, so an agent that asked its questions together was refused for asking well. This replaces the 0.1 rule, in which Elephant Goldfish included `AskPerson(max_questions=3)`.

## A contest that cites what anyone can check may settle a finding

**Decided.** The `contest` tool takes `cites`: `path:symbol` for code, or `field:name` for a part of the author's output. Code checks the citation: that the field is on the output, or that the path exists in the run's workspace. With `ElephantGoldfish(settle_contests=True)`, a contest whose citation checks out settles its finding without a person. A settled finding stays settled: a later finding from the same goldfish with the same `where` and `kind` is settled at once and is not sent to the author again. Escalations always go to a person. The core default is unchanged: `settle_contests=False`, so every contest still goes to a person unless you choose otherwise. This narrows the rule in [Elephant Goldfish](elephant-goldfish.md#findings-and-answers) that code never waves a contested finding through, for those who choose it.

**Why.** A contest that cites a file anyone can open, or a field anyone can read, should not stop a run for a question code can answer. Code checks the citation, so a model never decides the gate. Keeping the old default means other users see no change unless they choose it.

**Rejected.** Sending every contested finding to a person, as 0.1 does, for the software factory's own review. It stops a run for questions code can answer. Also rejected: a contest that settles without a citation code can check. Then a model would wave its own finding through.

## Elephant Goldfish uses its skills by default

**Decided.** The method's guidance is written as four Agent Skills that ship with Elephant Goldfish, in the core distribution behind the `methodologies` extra: `design-interview` and `design-resolve` for the author, `goldfish-comprehension` and `goldfish-critique` for two goldfish. They speak of "the work" and "the request", never of software. The author is always offered its two through Pydantic AI Harness's `Skills`, and loads one when it needs it. `goldfish` defaults to `ElephantGoldfish.GOLDFISH`, comprehension and critique built from their skills with `Goldfish.from_skill`; each goldfish gets its skill's body as its instructions. `goldfish=()` means no review, and the author is still offered the skills. Elephant Goldfish offers only its own skills. This is the one core default that changes from 0.1, where `goldfish` was required.

**Why.** A person can read, review and change the method as documents, without touching Python, and share them with the plugins they came from. The method belongs to Elephant Goldfish, so a factory for briefs or reports gets the same interview and the same goldfish as the software factory. A goldfish whose whole job is one skill must not depend on a model choosing to load it, or it would review by no standard while the round still counts.

**Rejected.** Keeping the method as Python strings: it can only be read and changed in Python. Keeping the skills in the software package: the method would belong to one use of it. Letting each goldfish load its own skill on demand. A switch to turn the skills off: a team that passes its own goldfish gets exactly its own.

## A method belongs to a station, not a factory

**Decided.** The software package publishes `design_station(model, *, method=...)` and `design_review(model=None)`. `method` defaults to `design_review()`, Elephant Goldfish with the three design goldfish, `rounds=4`, `clean_rounds=2`, `settle_contests=True` and `to_text=render_markdown`; `None` gives a designer with no method. `SoftwareFactory(model, *, design=None, repairs=2, accept=None)` takes one keyword for each station, never a method. Its 0.1 keywords `rounds`, `goldfish` and `review_model`, and the attributes that copied its settings, `model`, `rounds`, `repairs`, `goldfish` and `review_model`, still work in 0.2 with a `DeprecationWarning`, and go in 0.3. The old keywords are mapped onto `design_review()` and refused together with `design=`.

**Why.** A factory will have several stations, each with its own review or none. A factory-wide method would either apply to every station or quietly to one. The same parts compose a factory of your own, `Factory("ours", [design_station(model), build])`, and the simple designer that Factory Evals compares, `design_station(model, method=ElephantGoldfish(goldfish=()))`, with nothing assembled by hand.

**Rejected.** `SoftwareFactory(model, method=...)`, one keyword that swaps the review: see Why.

## Skills sit on the designer

**Decided.** Skills are project context, so they sit on the designer, not inside the method. `design_station` gives the designer a plain `Skills` over the software package's `design-read-code` and `design-doc`, beside its method, so the designer is offered exactly four skills with review or without it. The third software skill, `goldfish-readiness`, builds the readiness goldfish. The method is attached before the designer's `Skills`, which has `duplicate_names="keep_first"`. Two `Skills` on one agent combine into one catalogue, Elephant Goldfish's included, and the first wins a name, so no later skill replaces a method skill. Goldfish never load the designer's skills.

**Why.** The software package's skills are not the method, and a designer without review still needs them. Pydantic AI Harness combines two `Skills` on one agent into one catalogue; this release checks that it does so with Elephant Goldfish's catalogue too, and that `keep_first` keeps the method's skill when a later catalogue has the same name. Attached the other way round, the run fails on the duplicate name.

**Rejected.** Routing the designer's skills through the method, `ElephantGoldfish(skills=...)`. It would need an off switch, and a separate function for a designer without review.

## The type is `Artifact`

**Decided.** The type is spelled `Artifact`, and these pages use "artifact" as a defined term. All other prose is British.

**Why.** It is what Python developers will type and search for, and code and prose should match.
