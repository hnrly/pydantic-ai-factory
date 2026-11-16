---
title: Design
description: The first station of the software factory. It drafts a design, has it reviewed by fresh goldfish, and waits for a person to approve it.
---

# Design

The Design station turns a mission into a design a person can approve and a builder can follow. A designer agent drafts it. Fresh reviewers read each draft, and the designer revises in the same conversation until a round of review is clean. Then a person approves.

## The design

| Field | For the to-do list |
|---|---|
| `title` | To-do list |
| `goal` | Keep track of tasks in a browser. |
| `behaviour` | Pressing Enter on a blank task adds nothing. |
| `acceptance_criteria` | **AC2** Ticked tasks stay ticked after a reload. |
| `exclusions` | Accounts, sharing between devices |
| `open_questions` | Anything the mission left undecided. |

Each field but `title` and `goal` is a list of short sentences. Acceptance criteria are numbered by their place, so the second is AC2. `render_markdown(design)` turns a design into Markdown. If the designer wrote a label such as "AC2:" itself, `render_markdown` drops it, so the label is not printed twice.

A design that still has open questions fails the station's gate, `no_open_questions`, and goes back to the designer, which asks you or settles them. After `repairs` tries, two by default, a person decides.

## The review

Three goldfish read every draft: `comprehension` explains it back, `critique` checks it against its goal, which restates the mission, and `readiness` asks whether a builder could start without questions. Their standards are `DESIGN_STANDARDS`, and the three together are `DESIGN_GOLDFISH`. On the to-do list's first draft, `critique` reported:

```text
r1-critique-1  Major  The design never says what happens to a blank task.
```

The designer revised. The second round was clean, so the run waited for a person.

## When the designer disagrees

The designer may contest a finding instead of fixing it. Then a person decides. The designer is told not to list a contested finding as an open question as well, because that question would fail `no_open_questions` and cost a second attempt for no reason:

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
