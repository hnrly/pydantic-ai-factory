---
title: Getting started
description: Build a small factory with a design station and a build station, approve the design, and let it build.
---

# Getting started

A factory is a line of stations, each doing one job, with a person approving the work that matters. This page builds one with two stations. `design` writes a short design and waits for you to approve it. `build` then writes the app, has a second agent review it, and hands on both the change it made and the review.

## Install

```bash
pip install "pydantic-ai-factory[coder]" "pydantic-ai-slim[anthropic]"
export ANTHROPIC_API_KEY=...
```

The core is built on [Pydantic AI Harness](https://pydantic.dev/docs/ai/harness/). The `coder` extra adds the harness's Coder, which the builder uses to write files. Run the sample in an empty folder: the app is written there.

## Your first factory

```python
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.capabilities import Hooks, LocalWorkspace
from pydantic_ai.workspaces import ReadOnlyWorkspace
from pydantic_ai_harness import Coder
from pydantic_ai_harness.filesystem import FileChangeRequestedEvent

from pydantic_ai_factory import Factory, MemoryStore, StationContext, station

designer = Agent(
    "anthropic:claude-opus-5-5",
    name="designer",
    instructions="Write a short design: what the app does, and how to check it works.",
    model_settings={"cache": True},
)
builder = Agent(
    "anthropic:claude-opus-5-5",
    name="builder",
    capabilities=[LocalWorkspace("."), Coder()],
)
reviewer = Agent(
    "anthropic:claude-opus-5-5",
    name="reviewer",
    instructions="Check the code against the design. Say what is wrong, if anything.",
    capabilities=[Coder(sub_agents=False)],
)


class Built(BaseModel):
    diff: str
    review: str


@station
async def build(ctx: StationContext, design: str) -> Built:
    changes: list[str] = []
    hooks = Hooks()

    @hooks.on.event(FileChangeRequestedEvent)
    async def keep(_, change: FileChangeRequestedEvent) -> None:
        changes.append(change.diff)

    built = await ctx.run_agent(builder, design, capabilities=[hooks])
    review = await ctx.run_agent(reviewer, design, workspace=ReadOnlyWorkspace(built.workspace))
    return Built(diff="\n".join(changes), review=review.output)


software = Factory("software", [station(designer, approval=True), build])
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
print(run.waiting.output)
#> One page, index.html. Type a task and press Enter to add it; tick its box to mark it done.

done = software.resume_sync(run, run.approve(by="ada"), store=store)
print(done.output.review)
#> The page matches the design: tasks can be added and ticked off. Nothing is wrong.
print(done.output.diff)
"""
--- a/index.html
+++ b/index.html
@@ -0,0 +1,3 @@
+<h1>To-do list</h1>
+<input placeholder="Add a task">
+<ul></ul>
"""
```

The designer wrote a design, and the factory stopped to wait for you, because the design station has `approval=True`. Nothing was running while it waited. When you approved, the design went to `build`. There the builder wrote `index.html`, and the reviewer read the same files, read-only, and checked them against the design.

`build` hands on a `Built` record: the change as a diff, and the review. The diff comes from the builder's own file tools. Before each write or edit, Harness announces the change with its diff, and the `Hooks` listener keeps it. A file changed by a shell command is not in the diff.

`station(designer, approval=True)` turns one agent into a station. `build` runs two agents, so it is written as a function.

`model_settings={"cache": True}` asks the provider to cache the stable start of each request, so the designer is not charged in full for the same instructions twice. `Coder` turns caching on for the builder and reviewer by itself.

`Coder(sub_agents=False)` takes away the reviewer's `delegate_task` tool. A sub-agent starts from a fresh context and reads the project again, so a review handed to one costs about twice as much and says nothing new. The builder keeps the tool.

`LocalWorkspace` runs the builder's commands on your machine, so it works on macOS and Linux but not on Windows. Use a [sandbox](https://pydantic.dev/docs/ai/harness/) on Windows, or for anything you would not run yourself.

## Next

- [Factories](factories.md) and [stations](stations.md): what you just built, in more detail.
- [Gates](gates.md): check the work with plain code before a person sees it.
- [The software factory](software-factory.md): a ready-made factory whose designs are reviewed by fresh goldfish. It also runs from your terminal.
