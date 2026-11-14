---
title: Agents in stations
description: How a station runs Pydantic AI agents with ctx.run_agent, and what the factory adds.
---

# Agents in stations

A station runs a Pydantic AI agent with `ctx.run_agent(agent, prompt)`. The agent is ordinary: the same agent runs on its own with `agent.run_sync(...)`. What `ctx.run_agent` adds is the record.

```python
from pydantic_ai import Agent

from pydantic_ai_factory import MemoryStore, StationContext, station

builder = Agent("anthropic:claude-opus-5-5", name="builder", instructions="Build what the design says.")
reviewer = Agent("anthropic:claude-opus-5-5", name="reviewer", instructions="Say what is wrong, if anything.")


@station
async def build(ctx: StationContext, design: str) -> str:
    await ctx.run_agent(builder, design)
    review = await ctx.run_agent(reviewer, design)
    return review.output


result = build.run_sync("One page, index.html, with a box to add tasks.", store=MemoryStore())
print(result.outcome)
#> not_judged
```

It returns Pydantic AI's own `AgentRunResult`, and passes any other argument, such as `deps=` or `workspace=`, to the agent's run.

## What it adds

`ctx.run_agent` fills in the arguments of `Agent.run` that a station would otherwise pass by hand, and keeps what comes back.

- **The conversation.** It stores the agent's Pydantic AI `Conversation`, keyed by the agent's `name`, and passes it back on a later attempt.
- **Questions to a person.** When the agent calls a tool that waits for a person, the run waits. The answer is passed back as Pydantic AI's `DeferredToolResults`. See [approvals](approvals.md#tool-calls).
- **Methodologies.** It collects the evidence each [methodology](methodologies.md) on the agent records, and adds the methodology's gate to the station's.
- **Retries.** It sizes the agent's output retry budget so a methodology's review rounds and ordinary validation retries do not starve each other.

Give every agent a distinct `name`. It is how the factory keeps each agent's conversation apart.

## Which agents a station runs

The factory lists every agent before anything runs, so it can check keys and costs, and so a resumed run can check it is the same work. You do not list them yourself: when `@station` decorates a function, it reads the names the function refers to and keeps every Pydantic AI `Agent` among them. For `build` above, `build.agents` holds `builder` and `reviewer`.

That finds agents by name. It cannot see one reached through a value, such as a dictionary lookup. Name those yourself:

```python
from pydantic_ai import Agent

from pydantic_ai_factory import MemoryStore, StationContext, station

bug_fixer = Agent("anthropic:claude-opus-5-5", name="bug_fixer")
idea_shaper = Agent("anthropic:claude-opus-5-5", name="idea_shaper")
BY_KIND = {"bug": bug_fixer, "idea": idea_shaper}


@station(agents=[bug_fixer, idea_shaper])
async def triage(ctx: StationContext, kind: str) -> str:
    result = await ctx.run_agent(BY_KIND[kind], "Ticked tasks come back after a reload.")
    return result.output


print(triage.run_sync("bug", store=MemoryStore()).outcome)
#> not_judged
```

If `ctx.run_agent` meets an agent the station does not know, it raises `StationDefinitionError` and says to name it in `agents=`.

## Later attempts

When work comes back to a station, because a gate failed it or a person asked for changes, `ctx.run_agent` carries on the agent's stored conversation. The agent is not sent its prompt again. It is told what went wrong:

```text
A person asked for changes to your last output.
ada: Blank tasks should not be added.
```

So the station's code is the same on every attempt. `ctx.feedback` holds the same reasons, if your own code needs them. [Gates](gates.md) shows a run that does this.

## When a run resumes

A station cannot pause in the middle of a line. When an agent waits for a person, the station stops; when the person answers, the station function runs again from the top. Calls to `ctx.run_agent` that already finished in this attempt return their stored result at once, without calling the model. They are matched by agent and order, and give back the evidence their methodologies recorded.

So keep the code between `ctx.run_agent` calls deterministic: it should do the same thing each time it runs. Put anything else, such as reading the clock or calling an outside service, inside an agent's tool or a separate station.

## The context

| Member | Gives |
|---|---|
| `ctx.run_agent(agent, prompt, **kwargs)` | Runs an agent, as above. |
| `ctx.record(payload)` | Records evidence for the gate. |
| `ctx.deps` | Whatever you passed as `deps=`: clients and settings. |
| `ctx.attempt` | Which attempt this is, from 1. |
| `ctx.feedback` | Why the work came back, on a later attempt. |
| `ctx.run_id` | The run, for your own logs. |

Write `StationContext[MyDeps]` to give `ctx.deps` a type.
