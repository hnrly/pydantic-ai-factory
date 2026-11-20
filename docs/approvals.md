---
title: Approvals
description: How a run waits for a person to approve work, settle an escalation or answer an agent, and how it carries on.
---

# Approvals

Some work should not move on until a person says so. When that happens the run stops and returns, and nothing keeps running while it waits. The person answers later, in the same process or another, and the run carries on from its record in the [store](stores.md).

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")

software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
print(run.waiting.kind)
#> approval
print(run.waiting.output)
#> One page, index.html. Type a task and press Enter to add it; tick its box to mark it done.

done = software.resume_sync(run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

`run.approve` returns an answer. The answer names its run, so `resume_sync` needs nothing else to find it. An answer is bound to the exact output it was made on. If the design is revised, the new design needs its own answer.

## Three reasons to wait

| `run.waiting.kind` | Why | Answered with |
|---|---|---|
| `"approval"` | The station has `approval=True`, and every gate passed. | `run.approve`, `run.request_changes` or `run.reject` |
| `"escalation"` | Something only a person should settle. | The same three |
| `"tool-calls"` | An agent asked a person something, or asked before an action. | `run.reply`, with Pydantic AI's `DeferredToolResults` |

## Approve, change or reject

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)

changed = software.resume_sync(
    run.request_changes(by="ada", note="Blank tasks should not be added."), store=store
)
print(changed.waiting.output)
#> One page, index.html. Type a task and press Enter to add it; tick its box to mark it done. Pressing Enter on a blank task adds nothing.

done = software.resume_sync(changed.reject(by="ada", note="We don't need this."), store=store)
print(done.outcome)
#> failed
```

`run.approve(by=...)`, `run.request_changes(by=..., note=...)` and `run.reject(by=..., note=...)` each return an answer: an `Approve`, a `Changes` or a `Reject`. Pass it to `factory.resume`. Asking for changes sends the work back to the station as one [rework](factories.md#rework); the agent carries on its conversation with the note. Rejecting ends the run.

The library records who answered, in `run.answers`. It does not check who they are: that is your application's sign-in.

## Escalation

An escalation asks a person to settle what code must not. It comes from a gate returning `escalate(...)`, from a station raising `Escalate("...")`, or from rework that ran out with `on_limit="escalate"`.

The same three verbs answer it. Approving accepts the work as it stands, and the record says a person overrode the gate. Asking for changes grants one more attempt. Rejecting ends the run.

## Tool calls

An agent can stop mid-run to ask a person something, or before an action that needs sign-off. Pydantic AI calls these deferred tool calls. Inside a station, they become a waiting run, answered with Pydantic AI's own types.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build the page, then publish it.")


@builder.tool_plain(requires_approval=True)
def publish_page(path: str) -> str:
    return f"{path} is live."


software = Factory("software", [station(builder)])
store = MemoryStore()

run = software.run_sync("A to-do list web page.", store=store)
call = run.waiting.requests.approvals[0]
results = run.waiting.requests.build_results(approvals={call.tool_call_id: True})
done = software.resume_sync(run.reply(by="ada", results=results), store=store)
print(done.output)
#> Published index.html.
print(done.answers[0].results.answered_by)
#> {'publish_page-1': 'ada'}
```

`run.reply` wraps Pydantic AI's `DeferredToolResults` in a `Reply` that names the run and who answered. It fills in `answered_by` for each call that does not already say who answered it. A reply needs exactly one result for each pending call: an approval in `approvals`, and anything else in `calls`.

The agent resumes in the same conversation and the tool runs. To let an agent ask questions, give it the `AskPerson` capability, which adds an `ask_person` tool. Each question waits like any tool call; `AskPerson(max_questions=3)` caps them.

`ctx.run_agent` lets the run wait by adding `DeferredToolRequests` to the agent's output type. Pydantic AI refuses that for an agent with output validators, so such an agent keeps its own output type and runs normally. To let it wait as well, put `DeferredToolRequests` in its `output_type` yourself.

## Answers

An answer is one of four records, told apart by `kind`: `Approve`, `Changes`, `Reject` and `Reply`. The type `Answer` is their union. Each names:

- the run, as `run_id`;
- the run's `revision` when the person saw it;
- the wait it answers: its `station`, the kind of wait in `waiting`, and the `work` on offer;
- who answered, as `by`, and when, as `at`.

Each of `run.approve`, `run.request_changes`, `run.reject` and `run.reply` copies the run's id and its wait. So you supply only who answered, what they said and, from a web page, the revision the person saw, as `revision=`. Leaving `revision` out uses the run's own, which is right for code that has just read the run.

An answer is plain JSON, so it can be queued and resumed in another process. `TypeAdapter(Answer)` dumps and loads it.

`factory.check(run, answer)` says why an answer does not fit, without running anything. It returns `None` when the answer fits, or an `AnswerProblem` with a `reason`. `stale` is true when the run has moved on since the answer's revision, which usually means someone else answered first:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)

adas = run.approve(by="ada")
bobs = run.reject(by="bob", note="Not now.")
software.resume_sync(adas, store=store)

problem = software.check(store.get_run_sync(run.run_id), bobs)
print(problem.stale)
#> True
```

`factory.resume` applies the same rules. It raises `RunConflict` for a stale answer, and `ApprovalRefused` for any other problem, and changes nothing either way.
