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

done = software.resume_sync(run, run.approve(by="ada"), store=store)
print(done.outcome)
#> passed
```

A decision is bound to the exact output it was made on. If the design is revised, the new design needs its own decision.

## Three reasons to wait

| `run.waiting.kind` | Why | Answered with |
|---|---|---|
| `"approval"` | The station has `approval=True`, and every gate passed. | `run.approve`, `run.request_changes` or `run.reject` |
| `"escalation"` | Something only a person should settle. | The same three |
| `"tool-calls"` | An agent asked a person something, or asked before an action. | Pydantic AI's `DeferredToolResults` |

## Approve, change or reject

```python
from pydantic_ai import Agent

from pydantic_ai_factory import Factory, MemoryStore, station

designer = Agent("anthropic:claude-opus-5-5", name="designer", instructions="Write a short design.")
software = Factory("software", [station(designer, approval=True)])
store = MemoryStore()
run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)

changed = software.resume_sync(
    run, run.request_changes(by="ada", note="Blank tasks should not be added."), store=store
)
print(changed.waiting.output)
#> One page, index.html. Type a task and press Enter to add it; tick its box to mark it done. Pressing Enter on a blank task adds nothing.

done = software.resume_sync(changed, changed.reject(by="ada", note="We don't need this."), store=store)
print(done.outcome)
#> failed
```

`run.approve(by=...)`, `run.request_changes(by=..., note=...)` and `run.reject(by=..., note=...)` each return a decision; pass it to `factory.resume`. Asking for changes sends the work back to the station as one [rework](factories.md#rework); the agent carries on its conversation with the note. Rejecting ends the run.

The library records who decided. It does not check who they are: that is your application's sign-in.

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
answer = run.waiting.requests.build_results(approvals={call.tool_call_id: True})
done = software.resume_sync(run, answer, store=store)
print(done.output)
#> Published index.html.
```

The agent resumes in the same conversation and the tool runs.

## Asking a person

To let an agent ask a person what its mission leaves open, give it the `AskPerson` capability. It adds an `ask_person` tool that asks in rounds. A round is up to four questions, asked together, and the run waits once for the whole round. Each question says why it matters and may offer up to three options, the recommended one first.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import AskPerson, Factory, MemoryStore, station

planner = Agent("anthropic:claude-opus-5-5", name="planner", capabilities=[AskPerson()])
software = Factory("software", [station(planner)])
store = MemoryStore()

run = software.run_sync("A to-do list web page.", store=store)
call = run.waiting.requests.calls[0]
print(run.waiting.requests.metadata[call.tool_call_id]["question"])
"""
1. Should ticked tasks survive a reload?
   Why it matters: If they do, the page must save them.
   a) Yes, in the browser's storage (recommended)
   b) No, a reload clears the list
2. Can a ticked task be unticked?
   Why it matters: It decides whether a tick can be undone.
"""
answer = run.waiting.requests.build_results(calls={call.tool_call_id: "1: yes. 2: yes."})
done = software.resume_sync(run, answer, store=store)
print(done.output)
#> One page. Ticked tasks are saved in the browser, and can be unticked.
```

Each call's metadata holds the round twice: `questions`, a list of dictionaries with `text`, `why_it_matters`, `options` and `recommended`, and `question`, the same round as numbered text. The person replies once, to the whole round, and the reply is the tool's result. If the agent splits a round across several calls in one response, they are still one round; reply to each call.

| Setting | Default | Means |
|---|---|---|
| `max_rounds` | 3 | Rounds the agent may ask. After the last, the tool tells it to carry on with its best judgement and say what it assumed. 0 takes the tool away. |
| `max_per_round` | 4 | Questions in one round. A round with more is refused with a message, and the agent asks again. A refused round does not count. |

A question is a `Question`: `text`, `why_it_matters`, `options`, at most three or none for an open question, and `recommended`, which must be 0 when given, so the recommended option always comes first.

`ctx.run_agent` lets the run wait by adding `DeferredToolRequests` to the agent's output type. Pydantic AI refuses that for an agent with output validators, so such an agent keeps its own output type and runs normally. To let it wait as well, put `DeferredToolRequests` in its `output_type` yourself.
