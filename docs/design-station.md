---
title: Design
description: The first station of the software factory. It drafts a design, has it reviewed by fresh goldfish, and waits for a person to approve it.
---

# Design

The Design station turns a mission into a design a person can approve and a builder can follow. A designer agent drafts it. Fresh reviewers read each draft, and the designer revises in the same conversation until two rounds of review in a row are clean. Then a person approves.

`design_station(model)` builds the station, and `SoftwareFactory(model)` runs it. Its review is `design_review()`, [Elephant Goldfish](elephant-goldfish.md) tuned for software designs; pass `method=` another, or `None` for a designer with no review at all. See [the software factory](software-factory.md#your-own-design-station).

## The design

A `Design` takes the shape of a design document: what will be built and why, how the finished build will be judged, and a plan a builder can follow without asking anything. It holds no code.

| Field | Holds | For the to-do list | Renders under |
|---|---|---|---|
| `title` | A short name | To-do list | `# To-do list` |
| `summary` | What, why and what next | Keep track of tasks in a browser. | The paragraph under the title |
| `problem` | The pain, and how it is handled today | Tasks live on paper and get lost. | `## The Problem` |
| `goals`, `non_goals` | Sentences | Add and tick tasks; accounts are out | `## Goals`, `## Non-Goals` |
| `open_questions` | Sentences | Anything the mission left undecided | `## Open Questions` |
| `assumptions` | Sentences | One person uses the page. | `## Assumptions` |
| `journeys` | `Journey`: `title`, `given`, `when`, `then` | Given an empty list, when Ada types a task and presses Enter, then it appears | `## Critical User Journeys` |
| `behaviour` | Sentences, at least one | Pressing Enter on a blank task adds nothing. | `## Behaviour` |
| `acceptance_criteria` | Sentences, at least one, numbered by place | **AC2** Ticked tasks stay ticked after a reload. | `## Acceptance Criteria` |
| `architecture` | Components, data flow, stack, hosting, data model | One page, with its script inline. | `## Technical Plan` |
| `decisions` | `Decision`: `topic`, `choice`, `reason`, `source` (`person`, `code`, `request` or `default`) | Storage: the browser's local storage, from the request | `### Decisions this plan rests on` |
| `alternatives` | `Alternative`: `title`, `pros`, `cons`, `rejected_because`, `rejected_by` (`person` or `drafting`) | A server with a database, rejected because sharing is out of scope | `## Alternatives Considered` |
| `risks` | `Risk`: `risk`, `mitigation` | Storage is cleared; say so on the page | `## Risks` |
| `changes` | `Change`: `path`, `action` (`create`, `modify` or `delete`), `what`, `why` | create `index.html` | `## Detailed Implementation Plan`, `### Changes` |
| `steps` | `Step`: `title`, `changes`, `depends_on`, `done_when` | Write the page; done when both criteria hold | `### Order of Work` |
| `verification` | `Check`: `kind` (`build`, `unit`, `lint`, `e2e` or `manual`), `check`, `proves` | e2e: add a task, tick it and reload. Proves: AC1, AC2 | `## Verification Plan` |

Acceptance criteria are numbered by their place, so the second is AC2, and a check's `proves` lists those numbers. `render_markdown(design)` turns a design into Markdown under those headings, always in that order. A section with nothing in it says "None.", so a person or a builder finds each part in the same place every time. If the designer wrote a label such as "AC2:" itself, `render_markdown` drops it, so the label is not printed twice.

A design stored before 0.2 still loads: its `goal` becomes `summary` and its `exclusions` become `non_goals`.

The designer asks for prompt caching, `model_settings={"cache": True}`. Each review round resends the whole conversation, so the provider can serve all but the newest part from its cache.

## The designer's skills

The designer's method is written as [Agent Skills](https://agentskills.io/specification), which it loads when it needs them with the `load_capability` tool. It is offered exactly four: two from Elephant Goldfish, and two from this package.

| Skill | Ships with | Carries |
|---|---|---|
| `design-interview` | Elephant Goldfish | What only a person can decide, and how to ask it in rounds. |
| `design-resolve` | Elephant Goldfish | How to answer a finding: fix, contest with a citation, or escalate. |
| `design-read-code` | This package | How to ground a design: what to read first, what to take from the code, and how to cite every path exactly. |
| `design-doc` | This package | The design template, field by field, the Alternatives Rule, the Zero-Decision Rule, and when a choice is an assumption or an open question. |

The designer's instructions say "Load design-read-code before you ask anything, and design-doc before you draft." With no review, `design_station(model, method=ElephantGoldfish(goldfish=()))`, the designer is offered the same four. A third skill in this package, `goldfish-readiness`, is a goldfish's, not the designer's.

Before drafting, and during review, the designer may ask you what the mission leaves open. It has [`AskPerson`](approvals.md#asking-a-person) beside Elephant Goldfish, so it asks in rounds: up to four questions at a time, each with why it matters and up to three options, the recommended one first. It may ask three rounds; the run waits once for each.

## The gate

Plain code, not a model, decides whether a builder could follow the design. The station's gate is `design_is_buildable`. It fails a design that still has open questions, then checks that:

1. Every acceptance criterion is proved by at least one verification item, and every AC number a verification item names exists.
2. Every path in a step is in `changes`, and every path in `changes` is in a step.
3. Every step that a step `depends_on` exists, and the steps have no loop.
4. Every decision has a reason and a source.
5. There is at least one alternative, and each has a reason for rejection.
6. No field says "TBD" or "TODO", in any case.
7. No path appears twice in `changes`.

Each failure names the item, such as `AC2 is proved by no verification item`. A design that fails goes back to the designer with those reasons. The designer asks you, or fixes them. After `repairs` tries, two by default, a person decides.

## The review

Three goldfish read every draft, as Markdown from `render_markdown`. Each one's job, kinds and standard are the body of its skill. Together they are `DESIGN_GOLDFISH`, and their standards `DESIGN_STANDARDS`.

| Goldfish | Sees | Kinds of finding | Standard | Ported from |
|---|---|---|---|---|
| `comprehension` | The design only | `unclear` | `clear`: someone who has never seen the mission can explain it back without guessing | Google's comprehension agent; coship-dev's `check-comprehension` |
| `critique` | The design, the mission and the explain-back | `gap`, `yagni`, `failure_mode`, `contradiction`, `risk`, `missing_alternative`, `unjustified_decision`; at most seven | `faithful`: everything the mission needs and nothing it does not, with a rejected alternative | Google's design critic and feature specification standard; coship-dev's `critique-design` |
| `readiness` | The design and the mission | `missing_path`, `contradicts_code` (always a Blocker), `nonexistent_reference`, `ambiguous_interface`, `decision_left_to_builder`, `no_oracle`, `order_conflict` | `buildable`, the Zero-Decision Rule: a builder could make the right change the first time without asking | Google's implementation readiness and codebase details agents; coship-dev's `check-readiness` |

Comprehension and critique are [Elephant Goldfish's own](elephant-goldfish.md#the-method-as-skills). Readiness is built from this package's `goldfish-readiness` skill with `Goldfish.from_skill`.

`design_review()` runs them for up to four rounds, until two clean rounds run in a row: after a clean round, the goldfish read the same draft once more. A contest that cites a field of the design, such as `field:non_goals`, settles its finding when code checks it. On the to-do list's first draft, `critique` reported:

```text
r1-critique-1  Major  gap  The design never says what happens to a blank task.
```

The designer revised. The second and third rounds were clean, so the run waited for a person.

## When the designer disagrees

The designer may contest a finding instead of fixing it. Then a person decides. The designer is told not to list a finding it sends to a person as an open question as well, because that question would fail `design_is_buildable` and cost a second attempt for no reason:

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory

software = SoftwareFactory("anthropic:claude-opus-5-5")
store = MemoryStore()

run = software.run_sync("A to-do list page. Keep it to one file.", store=store)
print(run.waiting.kind)
#> escalation
print(run.waiting.reasons)
#> ('clean_final_round: r1-readiness-1 contested: The design does not say where the styles go. (The mission asks for one file, so there is no separate stylesheet.)',)

done = software.resume_sync(run, run.approve(by="ada", note="One file is right."), store=store)
print(done.outcome, done.approvals[-1].overrode)
#> passed True
```

Approving accepts the design as it stands, and the record says a person overrode the review. Ada is not then asked to approve the same design a second time.

## Asking for changes

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory

software = SoftwareFactory("anthropic:claude-opus-5-5")
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
run = software.resume_sync(run, run.request_changes(by="ada", note="Add a way to delete tasks."), store=store)
print(run.waiting.kind, run.waiting.output.behaviour[-1])
#> approval Each task has a delete button that removes it.
```

The designer picks up its conversation, revises, and the new draft is reviewed again from scratch. Each request for changes sends the design back once, and you may ask as often as you need; to stop, reject it with `run.reject(by=..., note=...)`.
