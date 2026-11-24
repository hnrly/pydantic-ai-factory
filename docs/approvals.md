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
| `"tool-calls"` | An agent asked a person something, or asked before an action. | `run.reply`, `run.allow` or `run.deny` |

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

An agent can stop mid-run to ask a person something, or before an action that needs sign-off. Pydantic AI calls these deferred tool calls. Inside a station, they become a waiting run. `run.question` says what the agent asked, and `run.action` what it wants to do.

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
print(run.action)
#> publish_page(path='index.html')
done = software.resume_sync(run.allow(by="ada"), store=store)
print(done.output)
#> Published index.html.
```

The agent resumes in the same conversation and the tool runs. `run.deny(by="ada", note="Not on a Friday.")` stops the tool instead: the agent is told the note and carries on without it.

### Asking a person

To let an agent ask a person what its mission leaves open, give it the `AskPerson` capability. It is built on [Pydantic AI Harness's `AskUser`](https://pydantic.dev/docs/ai/harness/ask-user/), with no answerer, so the run waits instead of asking in the same process. The agent gets the `ask_user_question` tool. One call asks up to ten questions, each with a short `header`, the question, two to six options, and `multi_select` when more than one may be picked. The run waits once for every question the agent asked in one response: that is one round.

`AskPerson` adds two things to `AskUser`. Its instructions tell the agent to ask everything in one call, to say in each question why it matters, and to put the option it recommends first, with " (Recommended)" at the end of its label. And it caps the rounds: once `max_rounds` rounds, three by default, have been answered, the tool is taken away and the agent carries on. `AskPerson(max_rounds=0)` gives the agent no way to ask.

Each question is a `Question` in `run.questions`, with its `header`, its text, its `options` and `multi_select`. Answer it with one option's label, a list of labels for a `multi_select` question, or your own words: `run.reply("Keep them (Recommended)", by="ada")`. The answers to one call go back to the agent together, as `AskUser` makes them, `{header: [label]}`, with your own words in place of a label. [Operating](operating.md#answering-a-waiting-run) shows it end to end.

A tool of your own can wait for a person too: raise Pydantic AI's `CallDeferred` with `kind="question"` and the question as `question` in its metadata. Its `Question` has no header and no options, and you answer it in words.

An agent may ask more than one thing at once. Then `run.question` and `run.action` are `None`, and `run.questions` and `run.actions` list every question and call. Answer them all in one reply, with each question or action as a key, such as `first, second = run.questions`, then `run.reply({first: "Keep them (Recommended)", second: "Grey, please."}, by="ada")`. For an action, `True` allows it and `False` denies it.

A reply needs exactly one answer for each question and each call, so `run.reply` refuses one that misses a question, and names it. An answer that picks a label its question does not offer, or more than one label for a question that is not `multi_select`, is refused with the harness's reason. `run.reply("Yes.", by="ada")` on a run that waits on two questions is refused, and the error says to use the mapping. Each verb wraps Pydantic AI's `DeferredToolResults` in a `Reply` that names the run and who answered.

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
