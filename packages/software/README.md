# pydantic-ai-factory-software

The software factory for [Pydantic AI Factory](https://github.com/hnrly/pydantic-ai-factory).

This package holds a ready-made factory for building software. It depends on the generic core, `pydantic-ai-factory`. The core never depends on it, and a check in CI enforces that.

## The Design station

The first station turns a mission into a software design. A designer agent drafts it, fresh reviewers check every draft under the Elephant Goldfish method, the designer revises until a round of review is clean, and then a person approves it.

```python
from pydantic_ai_factory import MemoryStore
from pydantic_ai_factory_software import SoftwareFactory

software = SoftwareFactory("anthropic:claude-opus-5-5")
store = MemoryStore()

run = software.run_sync("A to-do list web page where I can add tasks and tick them off.", store=store)
print(run.waiting.kind, run.waiting.output.title)
#> approval To-do list
```

From a terminal, `factory run "A to-do list web page where I can add tasks and tick them off."` does the same and asks for your decision; add `--offline` to try it with no key.

To run it for real, install `pydantic-ai-factory-software[anthropic]` and set an Anthropic key. [The software factory](https://github.com/hnrly/pydantic-ai-factory/blob/main/docs/software-factory.md) and [Design](https://github.com/hnrly/pydantic-ai-factory/blob/main/docs/design-station.md) pages say more.

Build, Quality and Release are the next stations.
